"""
Japanese-card lookup via TCGdex (https://api.tcgdex.net/v2).

Pokemon TCG API (api.pokemontcg.io) is primarily English-side — it carries
JP set metadata but the images and prices come from the EN/TCGplayer side.
For Japanese cards we use TCGdex instead, which has:
  - Authentic JP card images (named with JP characters)
  - Cardmarket pricing in EUR — a reasonable proxy for the JP secondary market
  - Coverage of every modern JP-only set (Pokemon Card 151, White Flare, etc.)

Strategy in lookup_jp_card:
  1. Match the LLM's set_name to a known TCGdex set ID (small mapping table
     for the most common modern sets).
  2. Construct the card ID (set_id + localId) and direct-fetch.
  3. Falls back to TCGdex's name-search if direct lookup fails.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

import httpx

log = logging.getLogger(__name__)

TCGDEX_BASE = "https://api.tcgdex.net/v2/ja"
EUR_TO_USD = 1.10   # rough; refresh occasionally

# Map common LLM-returned JP set names → TCGdex set IDs. Add to this as you
# encounter new sets. Casefold-comparison happens at lookup time.
SET_NAME_ALIASES = {
    "151":                            "SV2a",
    "pokemon card 151":               "SV2a",
    "pokemon 151":                    "SV2a",
    "scarlet & violet 151":           "SV2a",
    "sv: 151":                        "SV2a",
    "ポケモンカード151":              "SV2a",
    "white flare":                    "SV11W",
    "sv: white flare":                "SV11W",
    "sv11w white flare":              "SV11W",
    "black bolt":                     "SV11K",
    "sv: black bolt":                 "SV11K",
    "battle partners":                "SV9a",
    "sv: battle partners":            "SV9a",
    "terastal festival ex":           "SV8a",
    "sv: terastal festival ex":       "SV8a",
    "shiny treasure ex":              "SV4a",
    "ruler of the black flame":       "SV3",
    "raging surf":                    "SV3a",
    "ancient roar":                   "SV4K",
    "future flash":                   "SV4M",
    "wild force":                     "SV5K",
    "cyber judge":                    "SV5M",
    "mask of change":                 "SV6",
    "stellar miracle":                "SV7",
    "super electric breaker":         "SV7a",
    "paradise dragona":               "SV8",
    # SV10 — "Glory of Team Rocket" (the slab label commonly reads
    # "GLORY/RKT. GANG" or "ROCKET GANG" — handle both forms)
    "glory of team rocket":           "SV10",
    "sv: glory of team rocket":       "SV10",
    "glory of team rocket gang":      "SV10",
    "rocket gang":                    "SV10",
    "ロケット団の栄光":               "SV10",
    "crimson haze":                   "SV5a",
    "sv: crimson haze":               "SV5a",
    "クリムゾンヘイズ":               "SV5a",
    # Common alt phrasings the LLM emits for already-mapped sets
    "terastal fes ex":                "SV8a",
    "battle partners ex":             "SV9a",
}


@dataclass
class JpCardResult:
    name: str
    set_name: Optional[str]
    set_id: Optional[str]
    card_number: Optional[str]
    image_url: Optional[str]
    image_url_large: Optional[str]
    rarity: Optional[str]
    market_price: Optional[float]   # USD-converted from Cardmarket EUR
    market_currency: str = "USD"
    source: str = "tcgdex"
    language: str = "japanese"

    def to_dict(self):
        return {
            "name": self.name,
            "set_name": self.set_name,
            "set_id": self.set_id,
            "card_number": self.card_number,
            "image_url": self.image_url,
            "image_url_large": self.image_url_large,
            "rarity": self.rarity,
            "market_price": self.market_price,
            "market_currency": self.market_currency,
            "source": self.source,
            "language": self.language,
        }


def _resolve_set_id(set_name: Optional[str]) -> Optional[str]:
    """Map a free-text set name to a TCGdex set ID via alias table."""
    if not set_name:
        return None
    key = set_name.strip().lower()
    if key in SET_NAME_ALIASES:
        return SET_NAME_ALIASES[key]
    # Try stripping common prefixes
    cleaned = re.sub(r"^(sv|s&v|scarlet\s*&\s*violet)[:\s]+", "",
                      key, flags=re.IGNORECASE).strip()
    if cleaned in SET_NAME_ALIASES:
        return SET_NAME_ALIASES[cleaned]
    return None


def _normalize_local_id(num: Optional[str]) -> Optional[str]:
    """TCGdex stores localId as the printed number, often zero-padded.
    For card '171/165' we want '171'. For '025/165' we want '025' (TCGdex
    keeps the leading zeros for some old promo-style numbers; for modern
    cards it's typically un-padded). Try both forms in the caller."""
    if not num:
        return None
    return num.split("/")[0].strip()


def _extract_market_usd(card: dict) -> Optional[float]:
    """Cardmarket pricing object → best representative USD value.

    Pricing keys (when present):
      avg, low, trend, avg1, avg7, avg30  (non-holo)
      avg-holo, low-holo, trend-holo, avg1-holo, avg7-holo, avg30-holo

    For holo/IR/SAR cards, the holo variant is the actual market value.
    Prefer avg-holo > trend-holo > avg30-holo > avg.
    """
    pricing = (card.get("pricing") or {}).get("cardmarket") or {}
    if not pricing:
        return None

    keys_priority = ["avg-holo", "trend-holo", "avg30-holo", "avg7-holo",
                     "avg", "trend", "avg30", "avg7"]
    for k in keys_priority:
        v = pricing.get(k)
        if v and v > 0:
            return round(float(v) * EUR_TO_USD, 2)
    return None


def _to_result(card: dict) -> JpCardResult:
    set_obj = card.get("set") or {}
    set_id = set_obj.get("id")
    local_id = card.get("localId")
    image_base = card.get("image")  # e.g. https://assets.tcgdex.net/ja/SV/SV2a/171
    image_small = f"{image_base}/low.webp" if image_base else None
    image_large = f"{image_base}/high.webp" if image_base else None
    return JpCardResult(
        name=card.get("name") or "",
        set_name=set_obj.get("name"),
        set_id=set_id,
        card_number=local_id,
        image_url=image_small,
        image_url_large=image_large,
        rarity=card.get("rarity"),
        market_price=_extract_market_usd(card),
    )


async def lookup_jp_card(name: str, set_name: Optional[str] = None,
                          card_number: Optional[str] = None) -> Optional[JpCardResult]:
    """Best-effort lookup against TCGdex for a Japanese card.

    Returns None if neither the alias-mapped set ID nor any name-search
    fallback yielded a confident match.
    """
    set_id = _resolve_set_id(set_name)
    local_id = _normalize_local_id(card_number)

    async with httpx.AsyncClient(timeout=10.0) as client:
        # 1. Direct lookup if we have both set_id and number
        if set_id and local_id:
            for candidate_id in [f"{set_id}-{local_id}", f"{set_id}-{local_id.lstrip('0') or local_id}"]:
                try:
                    r = await client.get(f"{TCGDEX_BASE}/cards/{candidate_id}")
                    if r.status_code == 200:
                        data = r.json()
                        if data.get("id"):
                            return _to_result(data)
                except httpx.HTTPError as e:
                    log.warning("tcgdex direct lookup failed for %s: %s", candidate_id, e)

        # 2. Fallback: name search (limited coverage on older cards)
        try:
            r = await client.get(f"{TCGDEX_BASE}/cards", params={"name": name.strip()})
            if r.status_code == 200:
                items = r.json()
                if isinstance(items, list) and items:
                    # Without a set ID hint, pick the most recent (highest set ID lexically
                    # among SV-prefixed sets, then anything else).
                    items.sort(key=lambda c: (
                        0 if (c.get("id", "").upper().startswith("SV")) else 1,
                        c.get("id", ""),
                    ), reverse=True)
                    # Fetch detail for the top candidate (search results are summary-only)
                    detail_r = await client.get(f"{TCGDEX_BASE}/cards/{items[0]['id']}")
                    if detail_r.status_code == 200:
                        return _to_result(detail_r.json())
        except httpx.HTTPError as e:
            log.warning("tcgdex name search failed: %s", e)

    return None
