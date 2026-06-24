"""
eBay Browse API — clean JSON catalog/sold-listing search for Pokemon cards.

Replaces the brittle HTML scraping in `ebay_lookup.py` for the catalog-fallback
use case. Browse API gives us:
  - Title, image URLs, condition, grader (slabbed cards)
  - Per-item current price (active listings)
  - Stable JSON schema, no anti-bot 403s
  - 5000 requests/day free tier

USED BY: `card_lookup.lookup_card` as the FINAL fallback when both Pokemon
TCG API and TCGdex return None. Catches uncataloged Pokemon Center promos,
First Partner Collection cards, Japanese exclusives, stamped reprints, etc.

ENV VARS:
  EBAY_APP_ID         — your developer App ID (Client ID)
  EBAY_CERT_ID        — your Cert ID (Client Secret) [enables auto token refresh]
  EBAY_ACCESS_TOKEN   — pre-generated access token [used if CERT_ID is missing;
                        expires every 2h, must be manually refreshed]

Either CERT_ID (recommended) or ACCESS_TOKEN must be set.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

# Reuse the sealed-product query types/filters from the main engine.
sys.path.insert(0, str(Path(__file__).parent.parent))
from pricing_engine import (
    SealedProductQuery,
    PRODUCT_TYPE_SEARCH_TERMS,
    is_relevant_sealed_title,
    sealed_title_extra_word_count,
)

log = logging.getLogger(__name__)

EBAY_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_BROWSE_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"

# Pokemon Trading Card Singles category — narrows results to just cards
# (drops sealed booster packs, accessories, plushies, etc.).
POKEMON_SINGLES_CATEGORY = "183454"

# Default marketplace. EBAY_US is the largest pool; switch to EBAY_GB / EBAY_DE
# / EBAY_JP for region-specific listings.
DEFAULT_MARKETPLACE = "EBAY_US"

# Token cache — process-local. The Client Credentials grant returns a token
# valid for 7200s (2h); refresh 60s before expiry to avoid mid-request 401s.
_TOKEN_CACHE: dict = {"value": None, "expires_at": 0.0}
_TOKEN_LOCK: Optional[asyncio.Lock] = None


def _get_lock() -> asyncio.Lock:
    global _TOKEN_LOCK
    if _TOKEN_LOCK is None:
        _TOKEN_LOCK = asyncio.Lock()
    return _TOKEN_LOCK


@dataclass
class EbayItem:
    """One Browse API hit, normalised for downstream consumption."""
    item_id: str
    title: str
    image_url: Optional[str]
    image_url_large: Optional[str]
    price_usd: Optional[float]
    currency: str
    condition: Optional[str]
    seller_country: Optional[str]
    item_url: str
    sold: bool                              # True for "Sold" listings (when sold flag is applied)

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "title": self.title,
            "image_url": self.image_url,
            "image_url_large": self.image_url_large,
            "price_usd": self.price_usd,
            "currency": self.currency,
            "condition": self.condition,
            "seller_country": self.seller_country,
            "item_url": self.item_url,
            "sold": self.sold,
            "source": "ebay-browse",
        }


# ---------------------------------------------------------------------------
# OAuth — Client Credentials grant
# ---------------------------------------------------------------------------

async def _refresh_token() -> Optional[str]:
    """Exchange APP_ID + CERT_ID for a fresh access token.

    Returns None if EBAY_CERT_ID is missing (caller should fall back to the
    pre-supplied EBAY_ACCESS_TOKEN env var).
    """
    app_id = os.environ.get("EBAY_APP_ID")
    cert_id = os.environ.get("EBAY_CERT_ID")
    if not app_id or not cert_id:
        return None

    basic = base64.b64encode(f"{app_id}:{cert_id}".encode()).decode()
    headers = {
        "Authorization": f"Basic {basic}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    body = {
        "grant_type": "client_credentials",
        "scope": "https://api.ebay.com/oauth/api_scope",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(EBAY_TOKEN_URL, headers=headers, data=body)
    except httpx.HTTPError as e:
        log.warning("eBay token refresh failed: %s", e)
        return None

    if r.status_code != 200:
        log.warning("eBay token refresh returned %s: %s",
                    r.status_code, r.text[:200])
        return None

    payload = r.json()
    token = payload.get("access_token")
    expires_in = int(payload.get("expires_in", 7200))
    _TOKEN_CACHE["value"] = token
    _TOKEN_CACHE["expires_at"] = time.time() + expires_in - 60  # 60s slack
    log.info("eBay token refreshed (expires in %ds)", expires_in)
    return token


async def _get_token() -> Optional[str]:
    """Return a valid access token. Refreshes if missing or near-expiry."""
    lock = _get_lock()
    async with lock:
        # Cached + not expired → use cache
        if (_TOKEN_CACHE["value"]
                and _TOKEN_CACHE["expires_at"] > time.time()):
            return _TOKEN_CACHE["value"]
        # Try refresh via Client Credentials
        token = await _refresh_token()
        if token:
            return token
        # Fall back to pre-supplied env token (expires every 2h, needs
        # manual refresh, but works for quick smoke testing).
        env_token = os.environ.get("EBAY_ACCESS_TOKEN")
        if env_token:
            _TOKEN_CACHE["value"] = env_token
            _TOKEN_CACHE["expires_at"] = time.time() + 7000   # assume ~2h
            return env_token
    return None


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def _parse_item(it: dict) -> EbayItem:
    """Convert one Browse API result into an EbayItem."""
    price_obj = it.get("price") or {}
    price_val = price_obj.get("value")
    try:
        price_usd = float(price_val) if price_val is not None else None
    except (TypeError, ValueError):
        price_usd = None

    image = (it.get("image") or {}).get("imageUrl")
    # Browse API images are usually .../s-l225.jpg ; the source quality
    # version is .../s-l1600.jpg. Substitute for a "large" variant.
    image_large = image.replace("s-l225.", "s-l1600.") if image else None

    return EbayItem(
        item_id=it.get("itemId") or "",
        title=it.get("title") or "",
        image_url=image,
        image_url_large=image_large,
        price_usd=price_usd,
        currency=price_obj.get("currency") or "USD",
        condition=it.get("condition"),
        seller_country=(it.get("itemLocation") or {}).get("country"),
        item_url=it.get("itemWebUrl") or "",
        sold=bool(it.get("itemEndDate")),    # ended listings have an end date
    )


# Simple in-process cache so repeat lookups (same card scanned again, same
# search re-run from the catalogue-fallback path) don't burn the 5000/day
# free-tier quota. 24h TTL — eBay prices don't shift meaningfully faster.
_SEARCH_CACHE: dict[str, tuple[float, list[EbayItem]]] = {}
_SEARCH_CACHE_TTL = 24 * 3600


async def median_relevant_price(
    name: str,
    set_name: Optional[str] = None,
    card_number: Optional[str] = None,
    *,
    language: str = "english",
    grade_company: Optional[str] = None,
    grade: Optional[float] = None,
    sample_size: int = 8,
    marketplace: str = DEFAULT_MARKETPLACE,
    sort: Optional[str] = None,
) -> Optional[dict]:
    """Trim-median of active-listing prices for this card.

    Builds a strong query string from name + number + set + language hints,
    fetches `sample_size×2` listings, filters out titles that don't mention
    the card name + number (eBay's relevance ranker drifts on sparse JP
    queries), then returns the trimmed median.

    For JP cards this is significantly more accurate than Cardmarket EUR,
    which lags reality by 6-12 months for newer sets. For EN cards it
    sanity-checks TCGplayer's auto-pulled prices.

    Returns dict: {
        "median_usd": float,
        "sample_size": int,        # actual relevant listings used
        "low_usd": float,
        "high_usd": float,
        "items": [EbayItem, ...]   # the picked listings, for the UI
    }
    """
    parts = [name.strip()]
    if card_number:
        parts.append(str(card_number).strip())
    if set_name:
        parts.append(set_name.strip())
    if language.lower() == "japanese":
        parts.append("Japanese")
    if grade_company and grade is not None:
        grade_str = str(int(grade)) if grade == int(grade) else str(grade)
        parts.append(f"{grade_company} {grade_str}")
    query = " ".join(p for p in parts if p)

    # Over-fetch so the filter step has room to drop irrelevant titles
    items = await search_items(query, limit=max(sample_size * 2, 20),
                                marketplace=marketplace, sort=sort)
    if not items:
        return None

    # Relevance filter — title must mention the Pokemon name AND the card
    # number (if provided). eBay's relevance ranking sometimes surfaces
    # other cards from the same set when the query is sparse.
    name_tokens = [t.lower() for t in name.split() if len(t) >= 3]
    num_str = (str(card_number).split("/")[0].strip() if card_number else "")
    relevant = []
    for it in items:
        t = it.title.lower()
        if name_tokens and not any(tk in t for tk in name_tokens):
            continue
        if num_str and num_str not in t:
            continue
        # Drop bulk lot listings — "156" in a title often refers to the lot
        # quantity ("Sylveon ex Japanese 156-card lot"), not the card number.
        if any(kw in t for kw in (" lot", " bulk", " bundle", "x lot", " set of")):
            continue
        if re.search(r'\b\d+\s*cards?\b', t) and "1 card" not in t:
            continue
        # If the user is searching for the JP version, drop Korean
        # listings (they're a different print with different prices).
        if language.lower() == "japanese" and "korean" in t:
            continue
        # Drop graded listings unless the user explicitly wanted graded.
        # (?<!\w) catches "PSA10" (no space) as well as "PSA 10".
        # TAG/ACE are JP graders. "graded" catches generic grading language.
        if not grade_company and re.search(
            r'(?<!\w)(psa|cgc|bgs|sgc|tag\s*\d|ace\s*\d|graded)\b', t
        ):
            continue
        if grade_company and grade_company.lower() not in t:
            continue
        if it.price_usd is None or it.price_usd <= 0:
            continue
        relevant.append(it)

    if len(relevant) < 2:
        return None

    prices = sorted(it.price_usd for it in relevant)
    n = len(prices)
    # 10% trim each end when sample >=5, else use everything
    trim = max(1, int(n * 0.10)) if n >= 5 else 0
    trimmed = prices[trim:n - trim] if trim else prices
    if not trimmed:
        return None

    median = trimmed[len(trimmed) // 2] if len(trimmed) % 2 else (
        trimmed[len(trimmed) // 2 - 1] + trimmed[len(trimmed) // 2]) / 2

    # Pick the listings closest to the median for display
    relevant_by_distance = sorted(relevant,
                                   key=lambda it: abs((it.price_usd or 0) - median))
    return {
        "median_usd": round(median, 2),
        "sample_size": len(trimmed),
        "raw_sample_size": n,
        "low_usd": min(trimmed),
        "high_usd": max(trimmed),
        "query": query,
        "items": relevant_by_distance[:sample_size],
    }


async def search_items(
    query: str,
    *,
    limit: int = 10,
    marketplace: str = DEFAULT_MARKETPLACE,
    category_id: Optional[str] = POKEMON_SINGLES_CATEGORY,
    sort: Optional[str] = None,
    condition_filter: Optional[str] = None,
) -> list[EbayItem]:
    """Search the Browse API. Returns a list of normalised EbayItem.

    Args:
        query: free-text card description (LLM name + set + number works well)
        limit: max results to return (eBay caps at 200; default 10)
        marketplace: EBAY_US, EBAY_GB, EBAY_JP, etc.
        category_id: defaults to Pokemon Trading Card Singles
        sort: "-price" for highest first, "price" for cheapest. None = relevance.
        condition_filter: comma-separated like "NEW,USED" (eBay condition codes)

    Returns [] when:
      - No auth token available
      - eBay returns 0 results
      - Network error (logged at warning)
    """
    if not query or not query.strip():
        return []
    cache_key = (
        f"{marketplace}|{category_id or ''}|{condition_filter or ''}|"
        f"{sort or ''}|{limit}|{query.strip().lower()}"
    )
    now = time.time()
    cached = _SEARCH_CACHE.get(cache_key)
    if cached and now - cached[0] < _SEARCH_CACHE_TTL:
        return cached[1][:limit]

    token = await _get_token()
    if not token:
        log.warning("eBay Browse API skipped — no token "
                    "(set EBAY_APP_ID + EBAY_CERT_ID or EBAY_ACCESS_TOKEN)")
        return []

    params: dict[str, str] = {
        "q": query.strip(),
        "limit": str(min(max(limit, 1), 200)),
    }
    if category_id:
        params["category_ids"] = category_id
    if sort:
        params["sort"] = sort
    if condition_filter:
        params["filter"] = f"conditions:{{{condition_filter}}}"

    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": marketplace,
        "Accept": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(EBAY_BROWSE_URL, params=params, headers=headers)
    except httpx.HTTPError as e:
        log.warning("eBay Browse fetch failed: %s", e)
        return []

    if r.status_code == 401:
        # Token expired between cache check and request — invalidate and retry once
        log.info("eBay 401 — invalidating cached token and retrying")
        _TOKEN_CACHE["expires_at"] = 0.0
        token = await _get_token()
        if not token:
            return []
        headers["Authorization"] = f"Bearer {token}"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(EBAY_BROWSE_URL, params=params, headers=headers)
        except httpx.HTTPError as e:
            log.warning("eBay Browse retry failed: %s", e)
            return []

    if r.status_code != 200:
        log.warning("eBay Browse %s: %s", r.status_code, r.text[:200])
        return []

    payload = r.json()
    items = [_parse_item(it) for it in (payload.get("itemSummaries") or [])]
    _SEARCH_CACHE[cache_key] = (now, items)
    log.info("eBay Browse: q=%r → %d items (total=%s)",
             query, len(items), payload.get("total"))
    return items


# ---------------------------------------------------------------------------
# Sealed product images
# ---------------------------------------------------------------------------

async def lookup_sealed_image(
    name: str,
    set_name: str,
    product_type: str,
    language: str = "english",
) -> Optional[EbayItem]:
    """Find a representative product photo for a sealed item.

    Sealed products (booster boxes, ETBs, tins, bundles...) aren't in the
    individual-card catalogues (Pokemon TCG API / TCGdex), so they have no
    `image_url` from those sources. Unlike single cards — where the same
    name can map to different printings with different artwork, so a
    name-only match's image can't be trusted — every copy of e.g. "Mega
    Evolution Elite Trainer Box" is identical factory-sealed retail
    packaging. Any matching active eBay listing's photo IS this product's
    image, so we search active listings instead of the card catalogues.

    Among relevant listings, prefers the one whose title most closely
    matches the requested product (fewest extra words) — see
    `pricing_engine.sealed_title_extra_word_count` for why this matters
    when a series (e.g. "Mega Evolution") has multiple sub-expansion ETBs.

    Returns None if no relevant listing with an image is found.
    """
    product_term = PRODUCT_TYPE_SEARCH_TERMS.get(product_type, "")
    parts = [set_name or name]
    if product_term:
        parts.append(product_term)
    if language.lower() == "japanese":
        parts.append("Japanese")
    query = " ".join(p for p in parts if p)

    items = await search_items(query, limit=10, category_id=None)
    sq = SealedProductQuery(name=name, set_name=set_name or name,
                             product_type=product_type, language=language)
    relevant = [it for it in items if it.image_url and is_relevant_sealed_title(it.title, sq)]
    if not relevant:
        return None
    return min(relevant, key=lambda it: sealed_title_extra_word_count(it.title, sq))


async def median_sealed_active_price(
    name: str,
    set_name: str,
    product_type: str,
    language: str = "english",
    sample_size: int = 8,
) -> Optional[dict]:
    """Trimmed median of active-listing prices for a sealed product.

    Final price fallback for `_refresh_sealed_price` (app.py) when
    PriceCharting has no catalogue entry for this product (e.g. promo
    League Battle Decks) and eBay's sold-listings HTML scrape — blocked by
    a hard 403 from eBay's anti-bot — returns nothing. Active asking prices
    run a bit high vs sold comps, but are far better than no price at all.

    Builds the same query as `lookup_sealed_image` (set_name + product-type
    term), then filters with `is_relevant_sealed_title` to drop
    opened/resealed/lot listings before computing the median — the generic
    `median_relevant_price` (single-card path) doesn't apply those filters
    and lets junk listings skew the result.

    Returns dict {"median_usd", "sample_size", "raw_sample_size", "low_usd",
    "high_usd", "query"}, or None if fewer than 2 relevant priced listings
    are found.
    """
    product_term = PRODUCT_TYPE_SEARCH_TERMS.get(product_type, "")
    parts = [set_name or name]
    if product_term:
        parts.append(product_term)
    if language.lower() == "japanese":
        parts.append("Japanese")
    query = " ".join(p for p in parts if p)

    items = await search_items(query, limit=max(sample_size * 2, 20), category_id=None)
    sq = SealedProductQuery(name=name, set_name=set_name or name,
                             product_type=product_type, language=language)
    relevant = [it for it in items
                if it.price_usd and it.price_usd > 0 and is_relevant_sealed_title(it.title, sq)]
    if len(relevant) < 2:
        return None

    prices = sorted(it.price_usd for it in relevant)
    n = len(prices)
    trim = max(1, int(n * 0.10)) if n >= 5 else 0
    trimmed = prices[trim:n - trim] if trim else prices
    if not trimmed:
        return None

    median = trimmed[len(trimmed) // 2] if len(trimmed) % 2 else (
        trimmed[len(trimmed) // 2 - 1] + trimmed[len(trimmed) // 2]) / 2

    return {
        "median_usd": round(median, 2),
        "sample_size": len(trimmed),
        "raw_sample_size": n,
        "low_usd": min(trimmed),
        "high_usd": max(trimmed),
        "query": query,
    }
