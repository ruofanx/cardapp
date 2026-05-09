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
