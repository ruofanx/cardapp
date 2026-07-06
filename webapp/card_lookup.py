"""
Card metadata + market-price lookup via Pokemon TCG API (https://pokemontcg.io).

Free, no auth needed for our volume. Returns image URLs and TCGplayer market
prices, so the UI can show thumbnails and auto-fill purchase / market value.

Used by:
  - /api/cards/search        (typeahead in the Add Card flow)
  - /api/identify enhancement  (after LLM identifies, look up market price)
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import asdict, dataclass
from typing import Optional

import httpx

log = logging.getLogger(__name__)

POKEMONTCG_BASE = "https://api.pokemontcg.io/v2"
# An API key gives higher rate limits but isn't required for personal use.
POKEMONTCG_API_KEY = os.environ.get("POKEMONTCG_API_KEY", "").strip()


@dataclass
class CardLookupResult:
    name: str
    set_name: Optional[str]
    card_number: Optional[str]
    image_url: Optional[str]
    image_url_large: Optional[str]
    rarity: Optional[str]
    market_price: Optional[float]            # USD, source-dependent (see `source`)
    tcg_id: Optional[str]                    # e.g. "swsh9-076" or "SV2a-171"
    language: str = "english"
    source: str = "tcgplayer"                # "tcgplayer" or "cardmarket-jp"
    variant: Optional[str] = None            # "1st Edition Holo", "Unlimited Holo",
                                              # "Reverse Holo", "Normal" — set when
                                              # the catalogue has 2+ distinct prints
                                              # of the same card and search splits
                                              # them into separate rows.
    live_prices: Optional[dict] = None       # Per-grade live prices from
                                              # PriceCharting (24h cached). Lets the
                                              # scan-screen grade/condition toggles
                                              # render real prices without extra
                                              # round-trips. None when PC has nothing.
                                              # Keys: "ungraded", "grade_7..9_5",
                                              # "psa_10", "cgc_10", "cgc_10_pristine",
                                              # "bgs_10", "bgs_10_black", "sgc_10",
                                              # "sgc_10_pristine", plus "source"+"url".

    def to_dict(self):
        return asdict(self)


def _headers():
    return {"X-Api-Key": POKEMONTCG_API_KEY} if POKEMONTCG_API_KEY else {}


# Variant text → preferred Pokemon TCG API price key, in priority order.
# Old holo rares carry BOTH `1stEditionHolofoil` and `holofoil` keys with
# very different prices — taking max() biases toward the 1st Edition number
# even when the user owns Unlimited. When variant gives us an edition hint,
# pick the right key; otherwise fall back to a sensible default.
_VARIANT_KEY_PREFS: list[tuple[str, list[str]]] = [
    # (substring to match in lowercased variant, preferred keys in priority order)
    #
    # For old WotC-era cards (Base/Jungle/Fossil/Gym/Neo/Team Rocket) the
    # API exposes BOTH `1stEditionHolofoil` AND `unlimitedHolofoil` with
    # very different prices. Modern cards just have `holofoil`. So every
    # holo-related variant tries `unlimitedHolofoil` BEFORE `holofoil` as
    # the "cheaper print fallback" — that's the right default for any
    # Holo variant where the user didn't explicitly say "1st Edition".
    ("1st",         ["1stEditionHolofoil", "1stEdition", "holofoil"]),
    ("first",       ["1stEditionHolofoil", "1stEdition", "holofoil"]),
    ("shadowless",  ["normal"]),
    ("unlimited",   ["unlimitedHolofoil", "holofoil", "normal"]),
    ("reverse",     ["reverseHolofoil"]),
    # "rare holo" / "holo" — try Unlimited first, then plain holofoil, then
    # last-resort the 1st Edition price. This fixes the Sabrina's Alakazam
    # bug where variant="Holo" was falling through to max() and picking
    # the $246 1st Ed price instead of the $63 Unlimited.
    ("rare holo",   ["unlimitedHolofoil", "holofoil", "1stEditionHolofoil"]),
    ("holofoil",    ["unlimitedHolofoil", "holofoil", "1stEditionHolofoil"]),
    ("holo",        ["unlimitedHolofoil", "holofoil", "1stEditionHolofoil"]),
    # Modern variants — usually only `holofoil` exists for these cards anyway
    ("alt art",                   ["holofoil"]),
    ("special illustration rare", ["holofoil"]),
    ("illustration rare",         ["holofoil"]),
    ("rainbow",                   ["holofoil"]),
    ("hyper",                     ["holofoil"]),
    ("promo",                     ["holofoil", "normal"]),
    # Prismatic Evolutions holo-pattern variants — separate PC pages, no
    # distinct TCGplayer key yet; prefer masterBallHolofoil if it ever appears.
    ("master ball",               ["masterBallHolofoil", "holofoil"]),
]


def _preferred_keys_for_variant(variant: Optional[str]) -> list[str]:
    if not variant:
        return []
    v = variant.lower()
    for needle, keys in _VARIANT_KEY_PREFS:
        if needle in v:
            return keys
    return []


def _extract_market_price(card: dict, variant: Optional[str] = None) -> Optional[float]:
    """Pick the right TCGplayer 'market' price for this card + variant.

    If the variant points to a specific price key (e.g. Unlimited →
    `holofoil`, 1st Edition → `1stEditionHolofoil`), return that. Otherwise
    fall back to a sensible default — for old cards with both 1stEdition
    and Unlimited keys present, prefer Unlimited (the cheaper print) because
    that's the more common case among collectors and the one users tend to
    forget to specify.
    """
    prices = (card.get("tcgplayer") or {}).get("prices") or {}

    if prices:
        # 1. If the variant maps to a specific key set, use the first match.
        for key in _preferred_keys_for_variant(variant):
            data = prices.get(key)
            if data and data.get("market"):
                return float(data["market"])

        # 2. No variant hint — pick a default. Prefer Unlimited prints
        # (unlimitedHolofoil > holofoil > normal). NEVER pick 1stEditionHolofoil
        # as the default — 1st Edition prices are 2-5× higher than Unlimited
        # and the user usually doesn't own a 1st Edition. The JP→EN fallback
        # also lands here (JP cards never had 1st Edition).
        for key in ("unlimitedHolofoil", "holofoil", "normal"):
            data = prices.get(key)
            if data and data.get("market"):
                return float(data["market"])

        # 3. Last resort — any market price we can find.
        candidates = [v.get("market") for v in prices.values() if v.get("market")]
        if candidates:
            return float(max(candidates))

    # Cardmarket fallback if TCGplayer is empty
    cm = (card.get("cardmarket") or {}).get("prices") or {}
    avg = cm.get("averageSellPrice") or cm.get("trendPrice")
    return float(avg) if avg else None


# --- Distinct-print variant explosion --------------------------------------
#
# WotC-era holos (Fossil Gengar, Team Rocket Dark Dragonite, etc.) carry
# BOTH `1stEditionHolofoil` and `unlimitedHolofoil` price keys in the
# Pokemon TCG API. They're genuinely different physical cards with very
# different market prices (often 2-3× apart). Modern cards similarly carry
# `holofoil` + `reverseHolofoil` or `normal` + `reverseHolofoil`. Treating
# them as one row labelled "Rare Holo" is the source of constant confusion —
# the user can't tell which print they're looking at.
#
# The fix: when a card has 2+ distinct print variants in the catalogue,
# `_explode_variants` returns one (label, price) tuple per print. The
# search path emits one CardLookupResult row per variant, each with the
# variant label populated, so the UI can render them as separate options.
_VARIANT_KEY_LABELS: dict[str, str] = {
    "1stEditionHolofoil": "1st Edition Holo",
    "unlimitedHolofoil":  "Unlimited Holo",
    "1stEditionNormal":   "1st Edition",
    "1stEdition":         "1st Edition",
    "unlimited":          "Unlimited",
    "reverseHolofoil":    "Reverse Holo",
    "holofoil":           "Holo",
    "normal":             "Normal",
}

# Canonical display order — 1st Edition first (usually the chase print),
# Unlimited second, modern variants after.
_VARIANT_DISPLAY_ORDER: list[str] = [
    "1st Edition Holo", "Unlimited Holo",
    "1st Edition",      "Unlimited",
    "Holo",             "Normal",
    "Reverse Holo",
]


def _explode_variants(card: dict) -> list[tuple[Optional[str], Optional[float]]]:
    """Return one (variant_label, market_price) tuple per distinct print.

    - 2+ distinct prints in the catalogue → multiple labelled entries
      (e.g. [("1st Edition Holo", 312.06), ("Unlimited Holo", 171.93)] for
      Team Rocket Dark Dragonite #5).
    - 1 print → a single (None, price) tuple — preserves legacy single-row
      behaviour so cards with only one print don't gain a noisy "Holo" label.
    - No TCGplayer prices → Cardmarket EUR fallback as a single unlabelled row.
    """
    prices = (card.get("tcgplayer") or {}).get("prices") or {}

    seen: set[str] = set()
    unique: list[tuple[str, float]] = []
    for key, label in _VARIANT_KEY_LABELS.items():
        d = prices.get(key)
        if not d or d.get("market") is None:
            continue
        if label in seen:
            continue
        seen.add(label)
        unique.append((label, float(d["market"])))

    if len(unique) >= 2:
        order = {lbl: i for i, lbl in enumerate(_VARIANT_DISPLAY_ORDER)}
        unique.sort(key=lambda t: order.get(t[0], 99))
        return [(lbl, price) for lbl, price in unique]

    if len(unique) == 1:
        return [(None, unique[0][1])]

    cm = (card.get("cardmarket") or {}).get("prices") or {}
    avg = cm.get("averageSellPrice") or cm.get("trendPrice")
    return [(None, float(avg) if avg else None)]


def _to_result(card: dict, variant: Optional[str] = None,
               variant_label: Optional[str] = None,
               market_price_override: Optional[float] = None) -> CardLookupResult:
    """Build a CardLookupResult from a Pokemon TCG API card dict.

    `variant_label` + `market_price_override` are set by search-path expansion
    so each variant row carries the right print label + the right per-variant
    price. When omitted, falls back to the legacy "pick one price key by
    variant hint" path used by single-card lookup.
    """
    images = card.get("images") or {}
    if market_price_override is not None:
        price = market_price_override
    else:
        price = _extract_market_price(card, variant=variant)
    return CardLookupResult(
        name=card.get("name") or "",
        set_name=(card.get("set") or {}).get("name"),
        card_number=card.get("number"),
        image_url=images.get("small"),
        image_url_large=images.get("large"),
        rarity=card.get("rarity"),
        market_price=price,
        tcg_id=card.get("id"),
        language="english",
        variant=variant_label,
    )


# --- Smart search vocabulary -----------------------------------------------
#
# The Pokemon TCG API does field-scoped exact/prefix matching, not typo-
# tolerant fuzzy search. We get most of the way there by:
#   1. Expanding community nicknames ("moonbreon" → real card identity)
#   2. Pulling rarity / set / variant terms out of the query
#   3. Searching by the remaining name tokens with prefix wildcards
#   4. Re-ranking results client-side by name similarity
#
# Community calls cards by nicknames that aren't on the printed card. The
# alias map is intentionally small — extend as Ro encounters new ones.
POPULAR_ALIASES = {
    # nickname → query string we'll use against the API
    "moonbreon":         'name:"Umbreon VMAX" set.name:"Evolving Skies" number:"215"',
    "rayquaza alt":      'name:"Rayquaza VMAX" set.name:"Evolving Skies" number:"218"',
    "giratina alt":      'name:"Giratina V" set.name:"Lost Origin" number:"186"',
    "lugia alt":         'name:"Lugia V" set.name:"Silver Tempest" number:"186"',
    "espeon alt":        'name:"Espeon VMAX" set.name:"Evolving Skies" number:"215"',
    "chien-pao alt":     'name:"Chien-Pao ex" set.name:"Paldean Fates"',
    "iono sir":          'name:"Iono" set.name:"Paldean Fates" number:"237"',
    "snorlax base 2":    'name:"Snorlax" set.name:"Base Set 2"',
    "moonbreon psa10":   'name:"Umbreon VMAX" set.name:"Evolving Skies" number:"215"',
    "shiny char":        'name:"Charizard" rarity:"Shiny Rare"',
    "charizard upper":   'name:"Charizard ex" set.name:"Obsidian Flames" number:"215"',
}

# Map common rarity / variant terms to TCG API rarity values.
# Order matters — longer phrases first so we don't shadow them with substrings.
RARITY_TERMS = [
    ("special illustration rare", "Special Illustration Rare"),
    ("special art rare",          "Special Illustration Rare"),
    ("alternate art",             "Special Illustration Rare"),
    ("alt art",                   "Special Illustration Rare"),
    ("illustration rare",         "Illustration Rare"),
    ("rainbow rare",              "Rare Rainbow"),
    ("rainbow",                   "Rare Rainbow"),
    ("hyper rare",                "Hyper Rare"),
    ("secret rare",               "Rare Secret"),
    ("gold secret",               "Rare Secret"),
    ("ultra rare",                "Ultra Rare"),
    ("shiny rare",                "Shiny Rare"),
    ("trainer gallery",           "Trainer Gallery Rare Holo"),
    ("sir",                       "Special Illustration Rare"),
    ("sar",                       "Special Illustration Rare"),
    ("ar",                        "Illustration Rare"),
    ("ir",                        "Illustration Rare"),
    # Older / generic holo terms — must come after longer phrases above so
    # they don't shadow "trainer gallery rare holo" etc. The API rarity for
    # classic WotC + EX-era holographic prints is "Rare Holo".
    ("rare holo",                 "Rare Holo"),
    ("holographic",               "Rare Holo"),
    ("holo",                      "Rare Holo"),
]

# A few common subtypes the user might type
SUBTYPE_TERMS = [
    ("vstar", "VSTAR"), ("vmax", "VMAX"),
    ("ex",    "ex"),    ("gx",   "GX"), (" v ", "V"),
    # Printing variants — strip from the name clause; no API subtype filter.
    # "1st edition articuno fossil" → name:"articuno*" set.name:"fossil*"
    ("1st edition", None), ("first edition", None),
    ("shadowless",  None), ("unlimited",     None),
    # Prismatic Evolutions holo-pattern — "umbreon master ball 59" → name:"umbreon*" number:"59"
    ("master ball", None),
]

# Common set-name fragments we recognise. The API accepts wildcards in set
# search, so even partial names work. Keep this short — for novel sets we
# fall through to name-only search.
KNOWN_SET_TERMS = [
    # Scarlet & Violet era
    "151", "evolving skies", "brilliant stars", "lost origin", "silver tempest",
    "obsidian flames", "paldean fates", "paradox rift", "twilight masquerade",
    "scarlet & violet", "sword & shield", "crown zenith", "trainer gallery",
    "darkness ablaze", "rebel clash", "vivid voltage", "battle styles",
    "fusion strike", "evolutions", "celebrations", "shining fates",
    "chilling reign", "astral radiance", "pokemon go", "stellar crown",
    "prismatic evolutions",
    # Mega Evolution / Journey Together era (2026)
    "mega evolution", "ascended heroes", "journey together", "white flare",
    "black bolt",
    # Classic WotC / e-Card / EX era — needed for chase cards like Fossil
    # Gengar Holo. "fossil" alone is enough; the API set search is wildcarded.
    "base set", "base", "jungle", "fossil", "team rocket",
    "gym heroes", "gym challenge",
    "neo genesis", "neo discovery", "neo revelation", "neo destiny",
    "legendary collection",
    "expedition", "aquapolis", "skyridge",
    # Wizards-era promo + small-distribution sets
    "wizards black star promos", "black star promos",
    "southern islands", "best of game",
    "nintendo black star promos",
    # Common EX / DPP shorthand
    "ruby & sapphire", "firered & leafgreen", "emerald", "deoxys",
    "diamond & pearl", "platinum", "heartgold & soulsilver",
    "black & white", "plasma storm", "plasma freeze", "plasma blast",
    "xy", "flashfire", "primal clash", "roaring skies", "breakthrough",
    "sun & moon", "burning shadows", "ultra prism", "team up",
    "unbroken bonds", "cosmic eclipse",
]

# Some user-friendly set terms map to set IDs rather than set names because
# the API set name is ambiguous. "Base Set" is stored as "Base" in the API —
# set.name:"base set*" matches "Base Set 2" and "Expedition Base Set" but
# NOT the original "Base" (id=base1). Using set.id gives an exact match.
_SET_TERM_TO_ID: dict[str, str] = {
    "base set": "base1",
}


def _expand_alias(query: str) -> str:
    """If the query exactly matches a known nickname, return its expansion."""
    key = query.strip().lower()
    return POPULAR_ALIASES.get(key, query)


# --- Manual catalog --------------------------------------------------------
#
# Famous cards the Pokemon TCG API doesn't index (e.g. movie promos, oddball
# Wizards-era distributions). Without these, search + identify both come back
# with no image_url, and the UI renders a blank thumbnail.
#
# Each image is mirrored under webapp/static/manual_images/ so we don't
# depend on a third-party host's hotlink policy or uptime. Keep this list
# deliberately short — only cards the live API genuinely can't return.
# Absolute URLs only — the frontend (port 5173) renders these directly
# in <img src=...>, so a relative "/static/..." path resolves against the
# wrong origin. pkmncards.com hosts the canonical Ancient Mew art with no
# hotlink protection and a public-cache header. A local mirror lives at
# webapp/static/manual_images/ancient_mew.jpg as a backup but isn't the
# primary source.
_PKMNCARDS_ANCIENT_MEW = (
    "https://pkmncards.com/wp-content/uploads/miscellaneous.ancient-mew-1.jpg"
)

MANUAL_CARDS: list[CardLookupResult] = [
    CardLookupResult(
        name="Ancient Mew",
        set_name="Wizards Black Star Promos",
        card_number=None,
        image_url=_PKMNCARDS_ANCIENT_MEW,
        image_url_large=_PKMNCARDS_ANCIENT_MEW,
        rarity="Promo",
        market_price=None,
        tcg_id="manual-ancient-mew",
        language="english",
        source="manual-catalog",
    ),
]


def _manual_search_hits(query: str) -> list[CardLookupResult]:
    """Find manual-catalog entries matching a free-text search query.

    Match rules — the card wins if EITHER:
      1. The user's query is an exact name, a prefix of the name, or the
         name (no spaces removed) appears in the query.
      2. Every distinctive token of the card name (>=3 chars) appears as a
         whole word in the user's query.

    Both rules require the user to mention what makes the card distinctive
    ("ancient" + "mew"), so single-token queries like "mew" don't drag the
    promo up over the real Mew cards from the API.
    """
    if not query:
        return []
    q = query.strip().lower()
    if not q:
        return []
    hits: list[CardLookupResult] = []
    for c in MANUAL_CARDS:
        name = c.name.lower()
        name_squashed = name.replace(" ", "")
        q_squashed = q.replace(" ", "")

        # Rule 1: direct name match (exact, prefix-of-name, or compact form)
        if name == q or name.startswith(q) or name_squashed == q_squashed:
            hits.append(c)
            continue

        # Rule 2: token match — every distinctive (>=3 char) card-name token
        # must appear as a whole word in the user's query. Whole-word means
        # "mew" doesn't match "mewtwo" — important when both cards exist.
        card_tokens = [t for t in name.split() if len(t) >= 3]
        if not card_tokens:
            continue
        if all(re.search(rf"\b{re.escape(t)}\b", q) for t in card_tokens):
            hits.append(c)
    return hits


def _manual_identity_hit(name: str, set_name: Optional[str] = None) -> Optional[CardLookupResult]:
    """Find a manual-catalog entry from an LLM-identified name (+ optional set).

    Reuses `_manual_search_hits` so name forms like "AncientMew" (no space)
    and "Mew (Ancient)" both resolve — LLM outputs vary in spacing/order.
    """
    if not name:
        return None
    # Build a haystack query from name + set so the search-style matcher has
    # the same vocabulary it uses in the search bar.
    query_parts = [name]
    if set_name:
        query_parts.append(set_name)
    query = " ".join(query_parts)
    hits = _manual_search_hits(query)
    return hits[0] if hits else None


def _extract_rarity(query: str) -> tuple[str, Optional[str]]:
    """Pop the first matching rarity term out of the query.
    Returns (cleaned_query, api_rarity_value)."""
    q = query
    for term, api_value in RARITY_TERMS:
        # Match as a whole word — case-insensitive
        pattern = re.compile(rf"(?:^|\s){re.escape(term)}(?:\s|$)", re.IGNORECASE)
        m = pattern.search(q)
        if m:
            return pattern.sub(" ", q, count=1).strip(), api_value
    return q, None


def _extract_set(query: str) -> tuple[str, Optional[str]]:
    """Pop a known set-name fragment out of the query.

    Matches the term as-is, AND a singular-of-each-word variant so
    "southern island" still maps to "southern islands". Always returns the
    canonical plural form from KNOWN_SET_TERMS — the API trailing-wildcard
    `set.name:"southern island*"` matches "Southern Islands" either way,
    but using the canonical form keeps logs + cache keys consistent.
    """
    q = query
    for term in sorted(KNOWN_SET_TERMS, key=len, reverse=True):
        pattern = re.compile(rf"(?:^|\s){re.escape(term)}(?:\s|$)", re.IGNORECASE)
        if pattern.search(q):
            return pattern.sub(" ", q, count=1).strip(), term
        # Singular variant — drop trailing 's' from each word
        singular = re.sub(r"s\b", "", term)
        if singular != term and len(singular) >= 3:
            pattern_s = re.compile(rf"(?:^|\s){re.escape(singular)}(?:\s|$)",
                                    re.IGNORECASE)
            if pattern_s.search(q):
                return pattern_s.sub(" ", q, count=1).strip(), term
    return q, None


def _extract_subtype(query: str) -> tuple[str, Optional[str]]:
    q = query
    for term, api_value in SUBTYPE_TERMS:
        pattern = re.compile(rf"(?:^|\s){re.escape(term.strip())}(?:\s|$)",
                              re.IGNORECASE)
        if pattern.search(q):
            return pattern.sub(" ", q, count=1).strip(), api_value
    return q, None


# Card-number patterns the user might type:
#   "14/18"   → number=14 (drop the denominator)
#   "037/172" → number=37 (drop leading zeros — the API stores plain integers)
#   "TG11"    → number=TG11 (keep alpha-prefix tokens as-is)
#   "BW24"    → number=BW24
# Only N/M form is taken as an unambiguous card-number signal. Bare numbers
# like "5" left alone — they're too ambiguous (year? set-id? quantity?).
_CARD_NUMBER_SLASH_RE = re.compile(
    r"(?:^|\s)(\d{1,3})\s*[/／]\s*\d{1,3}(?=\s|$)"
)
_CARD_NUMBER_ALPHA_RE = re.compile(
    r"(?:^|\s)([A-Z]{1,3}\d{1,3})(?=\s|$)",
    re.IGNORECASE,
)


def _extract_card_number(query: str) -> tuple[str, Optional[str]]:
    """Pop a card-number token from the query.

    Recognizes N/M ("14/18", "037/172") and alpha-prefix ("TG11", "SWSH102",
    "BW24") forms. Returns (cleaned_query, normalized_number). The number is
    formatted the way the Pokemon TCG API stores it — numeric numerators
    have leading zeros stripped.
    """
    m = _CARD_NUMBER_SLASH_RE.search(query)
    if m:
        num = str(int(m.group(1)))    # "037" → "37"
        cleaned = (query[:m.start()] + " " + query[m.end():]).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned, num
    m = _CARD_NUMBER_ALPHA_RE.search(query)
    if m:
        num = m.group(1).upper()
        cleaned = (query[:m.start()] + " " + query[m.end():]).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned, num
    return query, None


def _name_score(query: str, candidate: str) -> int:
    """Cheap name-similarity score for client-side re-ranking.
    Higher = better. Prefix match dominates; word-boundary inclusion next."""
    if not query or not candidate:
        return 0
    q, c = query.lower().strip(), candidate.lower()
    score = 0
    if c == q:
        score += 200
    if c.startswith(q):
        score += 100
    if q in c:
        score += 50
    # Reward each query word that appears in candidate
    for token in q.split():
        if token in c:
            score += 10
    return score


# Bound concurrent PriceCharting fetches so we don't fire a request per
# search-result row. PC tolerates moderate concurrency; this caps us at
# 6 in-flight at once, which is plenty when cache hits return instantly.
_PC_FETCH_SEMAPHORE: Optional[object] = None  # asyncio.Semaphore — created lazily


def _get_pc_semaphore():
    """Lazy-init the PC fetch semaphore (event loop has to exist first)."""
    global _PC_FETCH_SEMAPHORE
    if _PC_FETCH_SEMAPHORE is None:
        import asyncio
        _PC_FETCH_SEMAPHORE = asyncio.Semaphore(6)
    return _PC_FETCH_SEMAPHORE


# PriceCharting row label → live_prices key. Keep names stable so the
# frontend can index by toggle state without case juggling.
_PC_LABEL_TO_KEY: dict[str, str] = {
    "Ungraded":         "ungraded",
    "Grade 7":          "grade_7",
    "Grade 8":          "grade_8",
    "Grade 9":          "grade_9",
    "Grade 9.5":        "grade_9_5",
    "PSA 10":           "psa_10",
    "CGC 10":           "cgc_10",
    "CGC 10 Pristine":  "cgc_10_pristine",
    "BGS 10":           "bgs_10",
    "BGS 10 Black":     "bgs_10_black",
    "SGC 10":           "sgc_10",
    "SGC 10 Pristine":  "sgc_10_pristine",
}


async def _attach_live_prices(result: CardLookupResult,
                                per_card_timeout_s: float = 2.5) -> None:
    """Fetch PriceCharting grade prices for this result and stash on result.live_prices.

    Best-effort: failures (timeout, no PC page, network error) leave
    live_prices=None. Cache hits return instantly via the existing 24h
    SQLite cache in pricecharting_lookup.

    `per_card_timeout_s` caps a single card's PC fetch so a slow upstream
    doesn't hold up the whole search response. The scan-screen UI is more
    sensitive to total latency than to having every card's prices on the
    first paint — toggles re-issue the search anyway and warm-cache makes
    the second call near-instant.
    """
    if not result.set_name or not result.card_number:
        return
    # Skip manual-catalog cards — PC doesn't carry Ancient Mew etc.
    if (result.source or "").startswith("manual"):
        return
    try:
        import pricecharting_lookup as pc
    except ImportError:
        return

    urls = pc._candidate_urls(
        result.name, result.set_name, result.card_number,
        language=result.language or "english",
        variant=result.variant,
    )
    if not urls:
        return

    sem = _get_pc_semaphore()
    import asyncio
    try:
        async with sem:
            fetched = await asyncio.wait_for(
                pc._fetch_prices_with_cache(urls),
                timeout=per_card_timeout_s,
            )
    except asyncio.TimeoutError:
        log.info("live_prices fetch timed out (%.1fs) for %r",
                 per_card_timeout_s, result.name)
        return
    except Exception as e:
        log.warning("live_prices fetch failed for %r: %s", result.name, e)
        return
    if not fetched:
        return
    table, url = fetched

    live: dict = {}
    for label, key in _PC_LABEL_TO_KEY.items():
        v = table.get(label)
        if v is not None:
            live[key] = float(v)
    if not live:
        return
    live["source"] = "pricecharting"
    live["url"] = url
    # Include cover image so search filmstrip can show the actual card photo
    # (especially useful for holo-pattern variants like Master Ball where the
    # TCG API only has the standard artwork scan).
    img_base = pc._image_cache_get(url)
    if img_base:
        _, img_large = pc._cover_image_urls(img_base)
        if img_large:
            live["image_url"] = img_large
    result.live_prices = live


async def search_cards(query: str, limit: int = 20,
                       attach_live_prices: bool = True) -> list[CardLookupResult]:
    """Typeahead-style search.

    Smart query parsing:
      - "moonbreon"               → alias-expanded direct lookup
      - "char rainbow"            → name:char* rarity:"Rare Rainbow"
      - "151 pikachu"             → name:pikachu* set.name:"*151*"
      - "umbreon alt art"         → name:umbreon* rarity:"Special Illustration Rare"
      - "charizard"               → plain prefix search
    """
    raw = query.strip()
    if len(raw) < 2:
        return []

    # Normalise bracket/hash notation users copy from PriceCharting or type naturally:
    #   "Umbreon [Master Ball] #59"  →  "Umbreon Master Ball 59"
    # Removes [brackets] (keep inner content) and strips leading # from numbers.
    raw = re.sub(r'\[([^\]]+)\]', r'\1', raw)   # "[Master Ball]" → "Master Ball"
    raw = re.sub(r'(?<!\w)#(\d+)', r'\1', raw)  # "#59" → "59"
    raw = re.sub(r'\s+', ' ', raw).strip()

    is_alias = False           # true → don't try typo fallback (alias is precise)
    name_part_for_typo = raw   # what to stem if the strict query yields nothing
    card_number: Optional[str] = None   # populated in the non-alias branch below;
                                        # used by the narrower-fallback in fetch loop
    name_part: str = ""
    holo_pattern_variant: Optional[str] = None  # e.g. "Master Ball" — injected on results below

    # 1. Alias expansion (e.g. "moonbreon" → full name+set+number query)
    expanded = _expand_alias(raw)
    if expanded != raw:
        api_q = expanded
        is_alias = True
    else:
        # 2. Pull card-number, rarity, set, subtype hints out of the raw query
        rest, card_number = _extract_card_number(raw)
        rest, rarity = _extract_rarity(rest)
        rest, set_hint = _extract_set(rest)
        rest_before_subtype = rest
        rest, subtype = _extract_subtype(rest)
        name_part = rest.strip()
        if "master ball" in rest_before_subtype.lower() and "master ball" not in rest.lower():
            holo_pattern_variant = "Master Ball"

        # Second-pass number extraction: after stripping set/subtype modifiers,
        # a trailing bare number (e.g. "umbreon 59" after stripping "master ball"
        # and "prismatic evolutions") is almost certainly a card number.
        # The primary `_extract_card_number` only accepts N/M or alpha-prefix forms
        # to avoid ambiguity on raw queries; here the context makes it safe.
        if card_number is None and name_part:
            _trailing_num = re.search(r'(?:^|\s)(\d{1,3})(?:\s|$)', name_part)
            if _trailing_num:
                card_number = str(int(_trailing_num.group(1)))
                name_part = (name_part[:_trailing_num.start()] + name_part[_trailing_num.end():]).strip()
                name_part = re.sub(r'\s+', ' ', name_part).strip()

        name_part_for_typo = name_part or raw

        clauses = []
        if name_part:
            clauses.append(f'name:"{_escape_lucene_phrase(name_part)}*"')
        if card_number:
            # number:"N" — the API stores plain integers, no denominator
            clauses.append(f'number:"{card_number}"')
        if rarity:
            clauses.append(f'rarity:"{rarity}"')
        if set_hint:
            if set_hint in _SET_TERM_TO_ID:
                # Some sets have ambiguous names in the API (e.g. the original
                # Base Set is named "Base", so "base set*" would also match
                # "Base Set 2" and "Expedition Base Set"). Use set.id for a
                # precise match when we have a known mapping.
                clauses.append(f'set.id:"{_SET_TERM_TO_ID[set_hint]}"')
            else:
                # Trailing-wildcard prefix match. The Pokemon TCG API 400s on
                # leading-wildcard patterns like `"*foo*"` (Lucene doesn't allow
                # `*` inside quoted phrases when it's the first char). `"foo*"`
                # works and is enough to match "Team Rocket Returns" from
                # "team rocket", "Base Set 2" from "base", etc.
                clauses.append(f'set.name:"{set_hint}*"')
        if subtype:
            clauses.append(f'subtypes:"{subtype}"')

        if not clauses:
            clauses.append(f'name:"{raw}*"')
        api_q = " ".join(clauses)

    params = {"q": api_q, "pageSize": min(limit * 3, 60),  # over-fetch for ranking
              "orderBy": "-set.releaseDate"}

    async with httpx.AsyncClient(timeout=15.0, headers=_headers()) as client:
        async def fetch(query_str: str, *, retries: int = 1) -> list[dict]:
            """Hit the TCG API with one retry on transient network errors.

            Distinguishes between "no data" (200 OK, empty results — the
            caller widens the search) and "request failed" (timeout, 5xx,
            connection reset — retry with backoff, then return empty so
            the caller's narrower fallbacks fire BEFORE the typo widen).
            """
            attempt = 0
            while True:
                try:
                    r = await client.get(f"{POKEMONTCG_BASE}/cards",
                                          params={**params, "q": query_str})
                    r.raise_for_status()
                    return r.json().get("data", [])
                except httpx.HTTPError as e:
                    if attempt < retries:
                        attempt += 1
                        import asyncio
                        await asyncio.sleep(0.4 * attempt)
                        continue
                    log.warning("Pokemon TCG search failed: %s (query=%r)",
                                e, query_str)
                    return []

        items = await fetch(api_q)

        # Narrower fallback BEFORE typo widening: if we had a strong signal
        # (card_number) but the strict query came back empty (possibly
        # because set.name was a typo or the API briefly hiccuped), retry
        # WITHOUT set.name but KEEP the number. Number alone is usually
        # unique enough within a single Pokemon's namespace.
        if not items and not is_alias and card_number and name_part:
            narrower = f'name:"{name_part}*" number:"{card_number}"'
            items = await fetch(narrower)
            if items:
                log.info("narrower fallback (dropped set): %r → matched %d cards",
                         narrower, len(items))

        # Typo fallback: if even the narrower query returned nothing, retry
        # with a shorter stem of the name part. "charzard" → "char" matches
        # Charizard. Alias-driven queries are skipped (they're already
        # precise). Word ≥5 chars only — short queries are too ambiguous.
        if not items and not is_alias and len(name_part_for_typo) >= 5:
            stem = name_part_for_typo[:max(3, len(name_part_for_typo) // 2)]
            items = await fetch(f'name:"{stem}*"')
            if items:
                log.info("typo fallback: %r → %r matched %d cards",
                         raw, stem, len(items))

        # First-word fallback: queries like "charmander first partner 30th" have
        # extra words that don't appear in card names. Drop everything after the
        # first word and retry. Only fires if name_part is multi-word AND still
        # empty after the typo fallback.
        if not items and not is_alias and ' ' in name_part:
            first_word = name_part.split()[0]
            items = await fetch(f'name:"{first_word}*"')
            if items:
                log.info("first-word fallback: %r → %r matched %d cards",
                         raw, first_word, len(items))

    # Client-side re-rank by name closeness to the original query
    items.sort(key=lambda c: _name_score(raw, c.get("name", "")), reverse=True)

    # Expand each catalogue card into 1 row per distinct print variant. A
    # WotC-era holo like Team Rocket Dark Dragonite becomes TWO rows —
    # "1st Edition Holo" ($312) and "Unlimited Holo" ($172) — so the user
    # picks the right print explicitly instead of guessing from a single
    # ambiguous "Rare Holo" line. Cards with only one print stay as a
    # single row with variant=None (no UI noise).
    api_results: list[CardLookupResult] = []
    for c in items[:limit]:
        for variant_label, variant_price in _explode_variants(c):
            api_results.append(_to_result(
                c,
                variant_label=variant_label,
                market_price_override=variant_price,
            ))
    api_results = api_results[:limit]

    # Propagate holo-pattern variants (e.g. "Master Ball") stripped from the
    # query so they flow into the stored card and PriceCharting URL builder.
    # Override plain holo/normal labels (user wants this specific pattern) but
    # preserve "Reverse Holo" (a distinct product) and printing distinctions
    # like "1st Edition"/"Shadowless" the user didn't ask for.
    if holo_pattern_variant:
        _overridable_variants = {None, "Holo", "Rare Holo", "Normal", "Holofoil"}
        for r in api_results:
            if r.variant in _overridable_variants:
                r.variant = holo_pattern_variant

    # Attach live (PriceCharting) per-grade prices to each result so the
    # scan-screen grade/condition toggles can render real numbers instantly
    # instead of multiplying base × static factor client-side. Per-card
    # timeout bounds a single slow PC URL from hijacking the whole
    # response. Cache hits return instantly via the existing 24h cache.
    #
    # Caller can disable this (attach_live_prices=False) when the request
    # is latency-sensitive — e.g. the photo-OCR path of /api/identify where
    # the frontend transitions to a results sheet only after the response
    # lands. Grade prices for the picked card come from /api/refresh-price
    # on demand instead.
    if api_results and attach_live_prices:
        import asyncio

        async def _bounded(r: CardLookupResult) -> None:
            try:
                await asyncio.wait_for(_attach_live_prices(r), timeout=3.0)
            except asyncio.TimeoutError:
                log.info("live_prices per-card timeout for %r — leaving null",
                         r.name)
            except Exception as e:
                log.warning("live_prices error for %r: %s", r.name, e)

        await asyncio.gather(
            *[_bounded(r) for r in api_results],
            return_exceptions=True,
        )

    # Merge in manual-catalog hits (cards the Pokemon TCG API doesn't carry,
    # e.g. Ancient Mew). Dedupe against API hits by case-insensitive name so
    # the API entry wins if it ever shows up.
    manual_hits = _manual_search_hits(raw)
    if manual_hits:
        api_names = {r.name.lower() for r in api_results}
        manual_hits = [m for m in manual_hits if m.name.lower() not in api_names]
        if manual_hits:
            # Manual matches go first — they're targeted exact-name hits, while
            # the API results are token-prefix matches that may include the
            # query word as part of a different card name (e.g. "ancient mew"
            # surfacing "Ancient Crystal" via the "ancient" prefix).
            combined = manual_hits + api_results
            return combined[:limit]
    return api_results


async def search_jp_cards(name: str, limit: int = 15) -> list[CardLookupResult]:
    """Search TCGdex JP for cards matching `name`, with PriceCharting prices.

    Routes the frontend's TCGdex JA widening through the backend so that
    JP cards (e.g. Neo Genesis Lugia) get live_prices attached, instead of
    showing "—" in the filmstrip because the client-side searchTCGdex call
    bypasses _attach_live_prices.
    """
    import asyncio
    from tcgdex_lookup import search_jp_candidates

    jp_hits = await search_jp_candidates(name, limit)
    results: list[CardLookupResult] = []
    for jr in jp_hits:
        tcg_id = (f"{jr.set_id}-{jr.card_number}"
                  if jr.set_id and jr.card_number else None)
        results.append(CardLookupResult(
            name=jr.name,
            set_name=jr.set_name,
            card_number=jr.card_number,
            image_url=jr.image_url,
            image_url_large=jr.image_url_large,
            rarity=jr.rarity,
            market_price=jr.market_price,
            tcg_id=tcg_id,
            language="japanese",
            source="tcgdex",
        ))

    if results:
        async def _bounded(r: CardLookupResult) -> None:
            try:
                await asyncio.wait_for(_attach_live_prices(r), timeout=3.0)
            except asyncio.TimeoutError:
                log.info("live_prices timeout for JP card %r", r.name)
            except Exception as e:
                log.warning("live_prices error for JP %r: %s", r.name, e)

        await asyncio.gather(*[_bounded(r) for r in results], return_exceptions=True)

    return results


import re


def _escape_lucene_phrase(s: str) -> str:
    """Backslash-escape Lucene special characters inside a quoted phrase.

    The Pokemon TCG API silently 400s on names like "Team Rocket's Moltres ex"
    because the apostrophe is treated as a special char even within
    double-quoted phrases. Backslash-escape it (and a few other specials)
    so the query parses cleanly.
    """
    if not s:
        return s
    # Order matters — escape backslash first or it'll re-escape itself.
    out = s.replace("\\", "\\\\")
    for ch in ('"', "'"):
        out = out.replace(ch, "\\" + ch)
    return out


def _normalize_number(num: Optional[str]) -> Optional[str]:
    """The TCG API stores card_number as just the printed numerator, e.g. '137'
    not '137/086' and not '174/172'. It also strips leading zeros: a card
    printed '025/165' lives in the API as '25'. Some cards keep alpha
    prefixes ('TG11') — leave those alone."""
    if not num:
        return None
    head = num.split("/")[0].strip()
    if head.isdigit():
        return str(int(head))   # '025' → '25'
    return head                 # 'TG11' → 'TG11'


def _normalize_set(name: Optional[str]) -> Optional[str]:
    """LLMs often add 'SV:' / 'S&V:' / 'Scarlet & Violet:' prefixes that the
    Pokemon TCG API doesn't carry. Strip them so equality matches the API."""
    if not name:
        return None
    n = name.strip()
    n = re.sub(r"^(SV|S&V|Scarlet\s*&\s*Violet|SWSH|Sword\s*&\s*Shield)[:\s]+",
               "", n, flags=re.IGNORECASE)
    return n.strip()


# --- Variant ↔ rarity alignment --------------------------------------------
#
# When the LLM tags a photo as a chase variant (Full Art / Illustration Rare /
# Special Illustration Rare / Hyper / Rainbow / etc.) we want the catalogue
# lookup to match that print, not silently fall back to the base Rare print —
# the Pokemon TCG API often doesn't index the secret-rare tiers of brand-new
# sets for several weeks after release. Without this check the lookup would
# return the base print's image_url and the UI would show framed art for a
# card that's actually a full-art (this was the original N's Zekrom bug).

# Variant phrases that mean "this is NOT a plain rare". Subsstring match,
# lowercased. The plain "holo" / "rare holo" / "reverse" / "1st edition" /
# "unlimited" / "shadowless" cases are intentionally absent — those CAN map
# to a plain Rare Holo catalogue entry without it being a mismatch.
_CHASE_VARIANT_HINTS = (
    "full art", "alt art", "alternate art", "alternative art",
    "illustration rare", "special illustration rare", "special art rare",
    "rainbow rare", "rainbow", "hyper rare",
    "secret rare", "gold secret", "gold rare",
    "shiny rare", "shiny secret",
    " sir", " sar",                     # leading-space → don't match inside other words
    "trainer gallery",
    "black white rare", "black bolt rare", "white flare rare",
)

# Variant → preferred API rarity strings (best first). Used to boost score
# when a candidate's `rarity` matches what the LLM identified.
_VARIANT_RARITY_PREFS: list[tuple[str, list[str]]] = [
    # Order matters — longer / more specific phrases first. Each variant
    # phrase maps to API rarity strings in priority order. The Pokemon TCG
    # API uses BOTH "Ultra Rare" (modern, ~2020+) and "Rare Ultra" (legacy,
    # pre-2020) for full-art ex/V cards, so include both wherever Full Art
    # is the target tier.
    ("special illustration rare", ["Special Illustration Rare", "Illustration Rare"]),
    ("special art rare",          ["Special Illustration Rare", "Illustration Rare"]),
    ("alternate art",             ["Special Illustration Rare", "Illustration Rare"]),
    ("alt art",                   ["Special Illustration Rare", "Illustration Rare"]),
    ("sar",                       ["Special Illustration Rare"]),
    ("sir",                       ["Special Illustration Rare"]),
    ("illustration rare",         ["Illustration Rare"]),
    ("rainbow",                   ["Rare Rainbow"]),
    ("hyper rare",                ["Hyper Rare", "Rare Rainbow"]),
    ("gold",                      ["Rare Secret", "Hyper Rare"]),
    ("secret rare",               ["Rare Secret"]),
    ("shiny rare",                ["Shiny Rare", "Rare Shiny"]),
    ("trainer gallery",           ["Trainer Gallery Rare Holo"]),
    ("black white rare",          ["Black White Rare"]),
    # FA → Full Art / Ultra Rare. JP "FA" tier corresponds to the EN
    # "Ultra Rare" rarity (the full-illustration ex/V print without the
    # blown-up secret-rare backdrop). For Team Rocket's Moltres ex, FA is
    # sv10-208 ($6.12 Ultra Rare), NOT sv10-229 ($122.01 SIR).
    ("full art",                  ["Ultra Rare", "Rare Ultra", "Illustration Rare", "Full Art"]),
    ("ultra rare",                ["Ultra Rare", "Rare Ultra"]),
    ("fa",                        ["Ultra Rare", "Rare Ultra"]),
    ("promo",                     ["Promo"]),
    ("holo",                      ["Rare Holo"]),
]

# Rarities that mean "framed art, plain print". If the LLM says chase but the
# only catalogue match has one of these, reject the candidate.
_PLAIN_RARITIES = {"common", "uncommon", "rare", "rare holo"}


def _is_chase_variant(variant: Optional[str]) -> bool:
    if not variant:
        return False
    v = " " + variant.lower() + " "
    return any(h in v for h in _CHASE_VARIANT_HINTS)


def _expected_rarities_for_variant(variant: Optional[str]) -> list[str]:
    if not variant:
        return []
    v = variant.lower()
    for needle, rarities in _VARIANT_RARITY_PREFS:
        if needle in v:
            return rarities
    return []


def _score_match(card: dict, want_number: Optional[str], want_set: Optional[str],
                 want_variant: Optional[str] = None) -> int:
    """Higher = better match for the LLM's identification.

    Number is the strongest signal (e.g. '137' nails the print). Set name is
    secondary. Variant↔rarity alignment is a strong tiebreaker AND a hard
    rejection signal for chase-vs-plain mismatches.
    """
    score = 0
    api_number = (card.get("number") or "").strip().lower()
    api_set = ((card.get("set") or {}).get("name") or "").strip().lower()
    api_rarity = (card.get("rarity") or "").strip()
    api_rarity_lower = api_rarity.lower()

    if want_number and api_number == want_number.lower():
        score += 100
    number_mismatch = bool(want_number) and api_number != want_number.lower()
    if want_set:
        ws = want_set.lower()
        if api_set == ws:
            score += 50
        elif ws in api_set or api_set in ws:
            score += 20
            # Loose substring match (e.g. "Black Star Promos" inside
            # "Scarlet & Violet Black Star Promos") spans 200+ printings
            # across a decade, all sharing rarity "Promo" — neither signal
            # pinpoints a specific card. If the number we read off the
            # photo doesn't match THIS candidate either, don't let the
            # generic set+variant alignment alone clear MIN_LOOKUP_SCORE.
            if number_mismatch:
                score -= 100

    # Variant ↔ rarity alignment
    expected = _expected_rarities_for_variant(want_variant)
    if expected and api_rarity in expected:
        score += 80

    # Heavy penalty when LLM says "chase" but candidate is a plain print.
    # Pushes the score below MIN_LOOKUP_SCORE so we return None rather than
    # showing the base print's framed-art image for a full-art photo.
    if _is_chase_variant(want_variant) and api_rarity_lower in _PLAIN_RARITIES:
        score -= 200

    return score


# Minimum score to accept a candidate as the LLM's intended card. With a
# normalized card_number match we hit 100 — well above this floor.
MIN_LOOKUP_SCORE = 50


async def lookup_card(name: str, set_name: str | None = None,
                      card_number: str | None = None,
                      language: str = "english",
                      variant: str | None = None) -> Optional[CardLookupResult]:
    """Best-effort lookup for an identified card.

    For Japanese cards (language="japanese") we route through TCGdex which
    has authentic JP imagery + Cardmarket EUR pricing. Pokemon TCG API only
    carries EN imagery and TCGplayer pricing even for sets it labels as JP.

    Strategy for English:
      1. If we have a number, query name+number directly. That short-circuits
         to the small handful of cards that actually match (e.g. 3 Pikachus
         with #25 instead of 50 generic Pikachus where the right one isn't
         even in the page).
      2. If that returns nothing (e.g. LLM hallucinated a number), fall back
         to broad name search ordered by release date so recent prints sit
         first.
      3. Rank candidates by `_score_match`. Reject if best score < threshold —
         better to show "no catalogue image" than a confident wrong card.
    """
    name = (name or "").strip()
    if not name:
        return None

    # Manual catalog short-circuit — cards the Pokemon TCG API doesn't index
    # at all (e.g. Ancient Mew, movie promos). The API would return nothing
    # for these, so /api/identify used to come back with image_url=null.
    # Check before any API call: if the LLM identified one of these, we have
    # the canonical image right here.
    manual = _manual_identity_hit(name, set_name)
    if manual:
        return manual

    # JP path: try TCGdex first; on failure fall through to the EN catalogue
    # so we at least get *some* metadata (with a clear mismatch warning later).
    if language.lower() == "japanese":
        from tcgdex_lookup import lookup_jp_card
        jp = await lookup_jp_card(name, set_name, card_number)
        if jp:
            return CardLookupResult(
                name=jp.name or name,
                set_name=jp.set_name,
                card_number=jp.card_number,
                image_url=jp.image_url,
                image_url_large=jp.image_url_large,
                rarity=jp.rarity,
                market_price=jp.market_price,
                tcg_id=f"{jp.set_id}-{jp.card_number}" if jp.set_id else None,
                language="japanese",
                source="cardmarket-jp",
            )
        # JP-not-found falls through to EN search below — caller can detect
        # mismatched language and warn the user.

    norm_number = _normalize_number(card_number)
    norm_set = _normalize_set(set_name)

    # If we have neither number nor set hint, we can't reliably disambiguate.
    if not norm_number and not norm_set:
        return None

    # Escape Lucene specials inside the quoted name. Apostrophe is the big
    # one — "Team Rocket's Moltres ex" was 400ing the API silently and the
    # lookup returned None. Backslash-escape inside the quoted phrase.
    safe_name = _escape_lucene_phrase(name)

    queries: list[tuple[str, dict]] = []
    if norm_number:
        # Number-strict query — usually narrows to ~1-5 candidates that are
        # actually plausible. orderBy keeps recent prints first if multiple match.
        queries.append((
            f'name:"{safe_name}" number:"{norm_number}"',
            {"pageSize": 25, "orderBy": "-set.releaseDate"},
        ))
    # Broad fallback in case the number was wrong / not in API yet
    queries.append((
        f'name:"{safe_name}"',
        {"pageSize": 50, "orderBy": "-set.releaseDate"},
    ))

    items: list[dict] = []
    async with httpx.AsyncClient(timeout=10.0, headers=_headers()) as client:
        for q, extra_params in queries:
            try:
                r = await client.get(
                    f"{POKEMONTCG_BASE}/cards",
                    params={"q": q, **extra_params},
                )
                if r.status_code != 200:
                    continue
                got = r.json().get("data", [])
                if got:
                    items = got
                    break
            except httpx.HTTPError as e:
                log.warning("Pokemon TCG lookup failed: %s", e)
                continue

    if not items:
        return None

    scored = sorted(
        ((_score_match(c, norm_number, norm_set, variant), c) for c in items),
        key=lambda t: t[0], reverse=True,
    )
    best_score, best_card = scored[0]

    # JP→EN fallback relaxation: when the LLM said the card is Japanese but
    # TCGdex didn't have it (set indexed only up to the main 98 cards, the
    # secret rare isn't ingested), we fall through to the English catalogue.
    # The EN equivalent has a DIFFERENT set name (e.g. "Destined Rivals" vs
    # "Glory of Team Rocket") and a DIFFERENT card number (the secret rare's
    # JP #112 is EN #208 or #229). So neither set nor number will score —
    # the only signal we trust is an exact name match, then VARIANT to pick
    # the right printing tier (SAR vs Hyper Rare vs base ex).
    if language.lower() == "japanese" and best_score < MIN_LOOKUP_SCORE:
        # Collect every EN candidate whose name exactly matches the LLM's
        wanted = name.strip().lower()
        name_matches = [c for _s, c in scored
                        if (c.get("name") or "").strip().lower() == wanted]
        if name_matches:
            # Pick the candidate whose rarity best matches the LLM's variant.
            # For "Special Art Rare" we want sv10-208 (SIR), not sv10-229
            # (Hyper Rare). For "Hyper Rare" / "Gold" we want sv10-229.
            # For plain ex we want sv10-31.
            expected = _expected_rarities_for_variant(variant)
            if expected:
                for cand in name_matches:
                    if (cand.get("rarity") or "").strip() in expected:
                        log.info("JP→EN fallback: name+variant match %r → %s "
                                 "(variant=%r rarity=%r)",
                                 name, cand.get("id"), variant,
                                 cand.get("rarity"))
                        return _to_result(cand, variant=variant)
            # No variant or no rarity match — fall back to first name-match.
            chosen = name_matches[0]
            log.info("JP→EN fallback: name-only match %r → %s "
                     "(variant=%r had no rarity match; rarity=%r)",
                     name, chosen.get("id"), variant, chosen.get("rarity"))
            return _to_result(chosen, variant=variant)

    if best_score < MIN_LOOKUP_SCORE:
        # FINAL FALLBACK: eBay Browse API. Catches Pokemon Center exclusives,
        # First Partner Illustration Collection, stamped reprints, and other
        # cards that neither Pokemon TCG API nor TCGdex has indexed. Returns
        # the listing's image + market price as a CardLookupResult so the
        # frontend renders SOMETHING the user can compare against their photo.
        try:
            ebay_hit = await _ebay_browse_fallback(
                name, set_name, card_number, variant,
            )
            if ebay_hit:
                return ebay_hit
        except Exception as e:
            log.warning("eBay Browse fallback failed: %s", e)

        log.info("lookup_card: no candidate scored ≥ %d for %r — best=%d "
                 "(variant=%r, rarity=%r). Returning None so the UI doesn't "
                 "show a misleading base-print image.",
                 MIN_LOOKUP_SCORE, name, best_score, variant,
                 best_card.get("rarity"))
        return None
    return _to_result(best_card, variant=variant)


async def _ebay_browse_fallback(
    name: str,
    set_name: Optional[str],
    card_number: Optional[str],
    variant: Optional[str],
) -> Optional[CardLookupResult]:
    """Query eBay Browse API for a card neither Pokemon TCG API nor TCGdex
    has indexed. Returns the first relevant listing as a CardLookupResult.

    Builds a search string that mirrors how collectors title their listings:
    NAME + SET (if available) + NUMBER (if numeric) + VARIANT. Filters to
    the Pokemon TCG Singles category so packs/accessories don't pollute.
    """
    try:
        import ebay_browse_api
    except ImportError:
        return None

    # Compose a search query that mirrors collector title conventions.
    parts: list[str] = [name.strip()]
    if set_name:
        parts.append(set_name.strip())
    if card_number:
        parts.append(str(card_number).strip())
    if variant and variant.strip().lower() not in {"none", "null"}:
        parts.append(variant.strip())
    query = " ".join(p for p in parts if p)

    items = await ebay_browse_api.search_items(query, limit=10)
    if not items:
        return None

    # Filter to titles that actually mention the Pokemon name (eBay's
    # relevance ranker sometimes drifts on niche queries).
    name_tokens = [t.lower() for t in name.split() if len(t) >= 3]
    relevant = [
        it for it in items
        if not name_tokens or any(t in it.title.lower() for t in name_tokens)
    ]
    pick = relevant[0] if relevant else items[0]

    log.info("eBay Browse fallback hit: q=%r → %s ($%.2f) %s",
             query, pick.title[:60], pick.price_usd or 0, pick.item_id)

    return CardLookupResult(
        name=name,
        set_name=set_name,
        card_number=card_number,
        image_url=pick.image_url,
        image_url_large=pick.image_url_large or pick.image_url,
        rarity=variant or "Promo",
        market_price=pick.price_usd,
        tcg_id=f"ebay-{pick.item_id}",
        language="english",
        source="ebay-browse",
        variant=variant,
    )
