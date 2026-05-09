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
    ("1st",         ["1stEditionHolofoil", "1stEdition", "holofoil"]),
    ("first",       ["1stEditionHolofoil", "1stEdition", "holofoil"]),
    ("shadowless",  ["normal"]),
    ("unlimited",   ["holofoil", "unlimitedHolofoil", "normal"]),
    ("reverse",     ["reverseHolofoil"]),
    # "rare holo" / "holo" → Unlimited print is the standard for old cards
    ("rare holo",   ["holofoil", "unlimitedHolofoil"]),
    ("holofoil",    ["holofoil"]),
    ("holo",        ["holofoil"]),
    # Modern variants — usually only `holofoil` exists for these cards anyway
    ("alt art",                   ["holofoil"]),
    ("special illustration rare", ["holofoil"]),
    ("illustration rare",         ["holofoil"]),
    ("rainbow",                   ["holofoil"]),
    ("hyper",                     ["holofoil"]),
    ("promo",                     ["holofoil", "normal"]),
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

        # 2. No variant hint — pick a default. If both 1stEditionHolofoil and
        # holofoil exist, prefer holofoil (Unlimited) so we don't quietly
        # over-price somebody's Unlimited card with the 1st Edition baseline.
        if "holofoil" in prices and prices["holofoil"].get("market"):
            return float(prices["holofoil"]["market"])
        if "normal" in prices and prices["normal"].get("market"):
            return float(prices["normal"]["market"])

        # 3. Last resort — any market price we can find.
        candidates = [v.get("market") for v in prices.values() if v.get("market")]
        if candidates:
            return float(max(candidates))

    # Cardmarket fallback if TCGplayer is empty
    cm = (card.get("cardmarket") or {}).get("prices") or {}
    avg = cm.get("averageSellPrice") or cm.get("trendPrice")
    return float(avg) if avg else None


def _to_result(card: dict, variant: Optional[str] = None) -> CardLookupResult:
    images = card.get("images") or {}
    return CardLookupResult(
        name=card.get("name") or "",
        set_name=(card.get("set") or {}).get("name"),
        card_number=card.get("number"),
        image_url=images.get("small"),
        image_url_large=images.get("large"),
        rarity=card.get("rarity"),
        market_price=_extract_market_price(card, variant=variant),
        tcg_id=card.get("id"),
        language="english",
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
]

# A few common subtypes the user might type
SUBTYPE_TERMS = [
    ("vstar", "VSTAR"), ("vmax", "VMAX"),
    ("ex",    "ex"),    ("gx",   "GX"), (" v ", "V"),
]

# Common set-name fragments we recognise. The API accepts wildcards in set
# search, so even partial names work. Keep this short — for novel sets we
# fall through to name-only search.
KNOWN_SET_TERMS = [
    "151", "evolving skies", "brilliant stars", "lost origin", "silver tempest",
    "obsidian flames", "paldean fates", "paradox rift", "twilight masquerade",
    "scarlet & violet", "sword & shield", "crown zenith", "trainer gallery",
    "darkness ablaze", "rebel clash", "vivid voltage", "battle styles",
    "fusion strike", "evolutions", "celebrations", "shining fates",
    "chilling reign", "astral radiance", "pokemon go", "stellar crown",
    "prismatic evolutions",
]


def _expand_alias(query: str) -> str:
    """If the query exactly matches a known nickname, return its expansion."""
    key = query.strip().lower()
    return POPULAR_ALIASES.get(key, query)


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
    """Pop a known set-name fragment out of the query."""
    q = query
    for term in sorted(KNOWN_SET_TERMS, key=len, reverse=True):
        pattern = re.compile(rf"(?:^|\s){re.escape(term)}(?:\s|$)", re.IGNORECASE)
        if pattern.search(q):
            return pattern.sub(" ", q, count=1).strip(), term
    return q, None


def _extract_subtype(query: str) -> tuple[str, Optional[str]]:
    q = query
    for term, api_value in SUBTYPE_TERMS:
        pattern = re.compile(rf"(?:^|\s){re.escape(term.strip())}(?:\s|$)",
                              re.IGNORECASE)
        if pattern.search(q):
            return pattern.sub(" ", q, count=1).strip(), api_value
    return q, None


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


async def search_cards(query: str, limit: int = 20) -> list[CardLookupResult]:
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

    is_alias = False           # true → don't try typo fallback (alias is precise)
    name_part_for_typo = raw   # what to stem if the strict query yields nothing

    # 1. Alias expansion (e.g. "moonbreon" → full name+set+number query)
    expanded = _expand_alias(raw)
    if expanded != raw:
        api_q = expanded
        is_alias = True
    else:
        # 2. Pull rarity/set/subtype hints out of the raw query
        rest, rarity = _extract_rarity(raw)
        rest, set_hint = _extract_set(rest)
        rest, subtype = _extract_subtype(rest)
        name_part = rest.strip()
        name_part_for_typo = name_part or raw

        clauses = []
        if name_part:
            clauses.append(f'name:"{name_part}*"')
        if rarity:
            clauses.append(f'rarity:"{rarity}"')
        if set_hint:
            clauses.append(f'set.name:"*{set_hint}*"')
        if subtype:
            clauses.append(f'subtypes:"{subtype}"')

        if not clauses:
            clauses.append(f'name:"{raw}*"')
        api_q = " ".join(clauses)

    params = {"q": api_q, "pageSize": min(limit * 3, 60),  # over-fetch for ranking
              "orderBy": "-set.releaseDate"}

    async with httpx.AsyncClient(timeout=10.0, headers=_headers()) as client:
        async def fetch(query_str: str) -> list[dict]:
            try:
                r = await client.get(f"{POKEMONTCG_BASE}/cards",
                                      params={**params, "q": query_str})
                r.raise_for_status()
                return r.json().get("data", [])
            except httpx.HTTPError as e:
                log.warning("Pokemon TCG search failed: %s (query=%r)", e, query_str)
                return []

        items = await fetch(api_q)

        # Typo fallback: if a strict-prefix search returned nothing, retry
        # with a shorter stem of the name part. "charzard" → "char" matches
        # Charizard. Alias-driven queries are skipped (they're already
        # precise). Word ≥5 chars only — short queries are too ambiguous.
        if not items and not is_alias and len(name_part_for_typo) >= 5:
            stem = name_part_for_typo[:max(3, len(name_part_for_typo) // 2)]
            items = await fetch(f'name:"{stem}*"')
            if items:
                log.info("typo fallback: %r → %r matched %d cards",
                         raw, stem, len(items))

    # Client-side re-rank by name closeness to the original query
    items.sort(key=lambda c: _name_score(raw, c.get("name", "")), reverse=True)
    return [_to_result(c) for c in items[:limit]]


import re


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


def _score_match(card: dict, want_number: Optional[str], want_set: Optional[str]) -> int:
    """Higher = better match for the LLM's identification.

    Number is the strongest signal (e.g. '137' nails the print). Set name is
    secondary. Anything below a meaningful score gets rejected by the caller
    so we never silently swap in an unrelated card.
    """
    score = 0
    api_number = (card.get("number") or "").strip().lower()
    api_set = ((card.get("set") or {}).get("name") or "").strip().lower()

    if want_number and api_number == want_number.lower():
        score += 100
    if want_set:
        ws = want_set.lower()
        if api_set == ws:
            score += 50
        elif ws in api_set or api_set in ws:
            score += 20
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

    queries: list[tuple[str, dict]] = []
    if norm_number:
        # Number-strict query — usually narrows to ~1-5 candidates that are
        # actually plausible. orderBy keeps recent prints first if multiple match.
        queries.append((
            f'name:"{name}" number:"{norm_number}"',
            {"pageSize": 25, "orderBy": "-set.releaseDate"},
        ))
    # Broad fallback in case the number was wrong / not in API yet
    queries.append((
        f'name:"{name}"',
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
        ((_score_match(c, norm_number, norm_set), c) for c in items),
        key=lambda t: t[0], reverse=True,
    )
    best_score, best_card = scored[0]
    if best_score < MIN_LOOKUP_SCORE:
        log.info("lookup_card: no candidate scored ≥ %d for %r — best=%d",
                 MIN_LOOKUP_SCORE, name, best_score)
        return None
    return _to_result(best_card, variant=variant)
