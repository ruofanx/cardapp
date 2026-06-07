"""
eBay sold-listings price lookup for raw cards.

For Japanese cards in particular, Cardmarket EUR data can be stale for sets
they don't track well. eBay sold listings — both US ('Japanese' keyword)
and JP — are a closer signal of actual transacted prices.

This module:
  1. Hits eBay US's sold-listings page with browser headers.
  2. Parses with the existing pricing_engine regex.
  3. Returns the outlier-trimmed median + sample count.
  4. 24h SQLite cache to avoid hammering eBay.

If the US-with-Japanese-keyword search returns thin data, callers can fall
back to ebay.co.jp (different anti-bot regime, requires different headers).
That's noted for follow-up but not yet wired here.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

# Reuse the main engine's parser/aggregator
sys.path.insert(0, str(Path(__file__).parent.parent))
from pricing_engine import (
    CardIdentity, CardQuery, build_ebay_sold_url,
    parse_ebay_sold_html, aggregate_sales,
)

log = logging.getLogger(__name__)

CACHE_DB = Path(__file__).parent / "ebay_cache.sqlite"
CACHE_TTL_SECONDS = 24 * 3600

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0.0.0 Safari/537.36")


@dataclass
class EbaySoldResult:
    median_usd: float
    sample_size: int
    raw_sample_size: int
    period_days: int
    low_usd: float
    high_usd: float
    sold_url: str
    cached: bool


@dataclass
class EbaySoldListings:
    """Aggregated sales + the individual relevant sales rows.

    The frontend uses this to render a sortable table of recent comps; the
    aggregated median is still the headline number.
    """
    median_usd: Optional[float]
    sample_size: int                  # n after outlier trim
    raw_sample_size: int              # n before trim
    period_days: int
    low_usd: Optional[float]
    high_usd: Optional[float]
    sold_url: str
    cached: bool
    sales: list                        # list[dict] — see _sale_to_dict


@dataclass
class EbayRecentMean:
    """Mean of the N most-recent relevant sales.

    Used as the headline number for /api/refresh-price (both raw and graded).
    "Recent" here means selection by sold_date desc, then take the first N —
    so it weights the freshest comps, which matters because card prices
    move; a 90-day median can lag a real shift by weeks.
    """
    mean_usd: float
    median_usd: Optional[float]        # also computed, for comparison/UI
    sample_size: int                   # actual number averaged (≤ requested_n)
    requested_n: int
    raw_sample_size: int               # total relevant sales found in window
    period_days: int
    low_usd: float
    high_usd: float
    sold_url: str
    cached: bool
    is_graded: bool
    sales: list                        # the N sales used, newest first


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _init_cache():
    conn = sqlite3.connect(str(CACHE_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ebay_cache (
            url     TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            ts      REAL NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def _cache_get(url: str) -> Optional[dict]:
    _init_cache()
    conn = sqlite3.connect(str(CACHE_DB))
    row = conn.execute("SELECT payload, ts FROM ebay_cache WHERE url=?", (url,)).fetchone()
    conn.close()
    if not row:
        return None
    if time.time() - row[1] > CACHE_TTL_SECONDS:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return None


def _cache_set(url: str, payload: dict):
    _init_cache()
    conn = sqlite3.connect(str(CACHE_DB))
    conn.execute(
        "INSERT OR REPLACE INTO ebay_cache (url, payload, ts) VALUES (?, ?, ?)",
        (url, json.dumps(payload), time.time()),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Public lookup
# ---------------------------------------------------------------------------

def _sale_to_dict(sale) -> dict:
    """Serialise a SaleRecord into a JSON-friendly dict for the API response."""
    return {
        "price_usd": round(float(sale.price_usd), 2),
        # ISO date string; the frontend renders a relative "5d ago" label
        "sold_date": sale.sold_date.date().isoformat() if sale.sold_date else None,
        "title": sale.title,
        "url": sale.url,
        "source": sale.source or "ebay-us",
    }


async def lookup_sold_listings(
    name: str,
    set_name: str = "",
    card_number: str = "",
    language: str = "english",
    condition: str = "NM",
    is_graded: bool = False,
    grade_company: Optional[str] = None,
    grade: Optional[float] = None,
    variant: Optional[str] = None,
    period_days: int = 60,
    max_listings: int = 25,
) -> Optional[EbaySoldListings]:
    """Fetch eBay sold listings for this card, return BOTH the aggregated
    median and the individual sales rows (price + date + title + url).

    Used by the Detail screen's Sold Listings tab to render real recent comps
    filtered by language + grading. Cached per-search-URL for 24h.
    """
    identity = CardIdentity(
        name=name, set_name=set_name or "",
        card_number=card_number or "", language=language,
        variant=variant,
    )
    query = CardQuery(
        card=identity,
        is_graded=is_graded,
        grade_company=grade_company if is_graded else None,
        grade=grade if is_graded else None,
        condition=None if is_graded else condition,
    )
    url = build_ebay_sold_url(query)

    # Cache lookup — re-hydrate the SaleRecord dicts from JSON.
    cached = _cache_get(url)
    if cached and "sales" in cached:
        return EbaySoldListings(
            median_usd=cached.get("median_usd"),
            sample_size=cached.get("sample_size", 0),
            raw_sample_size=cached.get("raw_sample_size", 0),
            period_days=cached.get("period_days", period_days),
            low_usd=cached.get("low_usd"),
            high_usd=cached.get("high_usd"),
            sold_url=url,
            cached=True,
            sales=cached.get("sales", [])[:max_listings],
        )

    try:
        async with httpx.AsyncClient(timeout=15.0,
                                       headers={"User-Agent": USER_AGENT}) as client:
            r = await client.get(url, follow_redirects=True)
    except httpx.HTTPError as e:
        log.warning("eBay fetch failed: %s", e)
        return None

    if r.status_code != 200:
        log.warning("eBay returned %s for %s", r.status_code, url)
        return None

    from pricing_engine import is_relevant_title
    parsed = parse_ebay_sold_html(r.text, period_days=period_days)
    # Filter to relevant listings (skips proxies, lots, mismatched graders).
    relevant = [s for s in parsed if is_relevant_title(s.title, query)]
    # Sort by date desc so newest comps surface first.
    relevant.sort(key=lambda s: s.sold_date, reverse=True)

    quote = aggregate_sales(parsed, query, period_days=period_days)
    sale_dicts = [_sale_to_dict(s) for s in relevant]

    payload = {
        "median_usd": quote.median_usd if quote else None,
        "sample_size": quote.sample_size if quote else 0,
        "raw_sample_size": quote.raw_sample_size if quote else len(parsed),
        "period_days": period_days,
        "low_usd": quote.low_usd if quote else None,
        "high_usd": quote.high_usd if quote else None,
        "sales": sale_dicts,
    }
    _cache_set(url, payload)
    return EbaySoldListings(
        **{k: v for k, v in payload.items() if k != "sales"},
        sold_url=url,
        cached=False,
        sales=sale_dicts[:max_listings],
    )


async def lookup_recent_n_mean(
    name: str,
    set_name: str = "",
    card_number: str = "",
    language: str = "english",
    condition: str = "NM",
    is_graded: bool = False,
    grade_company: Optional[str] = None,
    grade: Optional[float] = None,
    variant: Optional[str] = None,
    n: int = 5,
    period_days: int = 90,
) -> Optional[EbayRecentMean]:
    """Fetch eBay sold listings, pick the N most-recent relevant sales,
    return their arithmetic mean as the headline price.

    Works for both raw (NM/LP/MP/HP/DMG) and graded queries — the underlying
    `lookup_sold_listings` already builds the right query string (including
    "PSA 10" terms for graded, "-PSA -BGS -CGC -SGC -graded -slab" exclusions
    for raw) and runs `is_relevant_title` to drop proxies, lots, and
    mismatched-grader listings.

    Returns None when:
      - eBay returns nothing for this card in the window
      - The HTTP request is blocked (sandbox anti-bot, 403, timeout)
    Callers should fall back to the legacy TCGplayer / PriceCharting paths.
    """
    listings = await lookup_sold_listings(
        name=name, set_name=set_name, card_number=card_number,
        language=language, condition=condition,
        is_graded=is_graded, grade_company=grade_company, grade=grade,
        variant=variant,
        period_days=period_days,
        max_listings=max(n * 2, 25),    # over-fetch a bit for display below
    )
    if not listings or not listings.sales:
        return None

    # `lookup_sold_listings` already sorted by sold_date desc and filtered
    # via is_relevant_title, so the first N entries are the N most-recent
    # listings that match this card + condition/grade. Defensive re-sort by
    # date in case a cached payload was serialised in a different order.
    sales_sorted = sorted(
        listings.sales,
        key=lambda s: s.get("sold_date") or "",
        reverse=True,
    )
    picked = sales_sorted[:n]
    prices = [float(s["price_usd"]) for s in picked if s.get("price_usd") is not None]
    if not prices:
        return None

    mean = sum(prices) / len(prices)
    # Cheap median for the picked window (UI can show both numbers).
    mid = sorted(prices)
    if len(mid) % 2:
        med = mid[len(mid) // 2]
    else:
        med = (mid[len(mid) // 2 - 1] + mid[len(mid) // 2]) / 2

    return EbayRecentMean(
        mean_usd=round(mean, 2),
        median_usd=round(med, 2),
        sample_size=len(prices),
        requested_n=n,
        raw_sample_size=listings.raw_sample_size,
        period_days=period_days,
        low_usd=min(prices),
        high_usd=max(prices),
        sold_url=listings.sold_url,
        cached=listings.cached,
        is_graded=is_graded,
        sales=picked,
    )


async def lookup_raw_price(name: str, set_name: str, card_number: str,
                            language: str = "english",
                            condition: str = "NM",
                            period_days: int = 60) -> Optional[EbaySoldResult]:
    """Fetch eBay US sold listings for this card, return aggregated median.

    Returns None if eBay returns nothing or anti-bot blocks the request.
    """
    identity = CardIdentity(
        name=name, set_name=set_name or "",
        card_number=card_number or "", language=language,
        variant=None,
    )
    query = CardQuery(card=identity, is_graded=False, condition=condition)
    url = build_ebay_sold_url(query)

    cached = _cache_get(url)
    if cached:
        return EbaySoldResult(**{**cached, "cached": True})

    try:
        async with httpx.AsyncClient(timeout=15.0,
                                       headers={"User-Agent": USER_AGENT}) as client:
            r = await client.get(url, follow_redirects=True)
    except httpx.HTTPError as e:
        log.warning("eBay fetch failed: %s", e)
        return None

    if r.status_code != 200:
        log.warning("eBay returned %s for %s", r.status_code, url)
        return None

    sales = parse_ebay_sold_html(r.text, period_days=period_days)
    quote = aggregate_sales(sales, query, period_days=period_days)
    if not quote:
        return None

    payload = {
        "median_usd": quote.median_usd,
        "sample_size": quote.sample_size,
        "raw_sample_size": quote.raw_sample_size,
        "period_days": quote.period_days,
        "low_usd": quote.low_usd,
        "high_usd": quote.high_usd,
        "sold_url": url,
    }
    _cache_set(url, payload)
    return EbaySoldResult(**payload, cached=False)
