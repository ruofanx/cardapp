"""
PriceCharting graded-card pricing.

PriceCharting publishes a per-card price table covering Ungraded, generic
Grade 7/8/9/9.5, and grader-specific PSA/CGC/BGS/SGC 10s plus exotic 'BGS 10
Black' / 'CGC 10 Pristine' rows. We scrape that table and cache results
locally for 24 hours.

Why scrape: PriceCharting's structured API costs $40/mo. The HTML page is
public and stable enough that a regex parser is reliable. Cache aggressively
to avoid hammering them.

URL pattern (English):
  https://www.pricecharting.com/game/pokemon-<set-slug>/<name-slug>-<number>

URL pattern (Japanese):
  https://www.pricecharting.com/game/pokemon-japanese-<set-slug>/<name-slug>-<number>

Slug guessing isn't perfect; we try a few variants per lookup.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger(__name__)

PC_BASE = "https://www.pricecharting.com"
CACHE_DB = Path(__file__).parent / "pricecharting_cache.sqlite"
CACHE_TTL_SECONDS = 24 * 3600  # 1 day

# Map (grader, grade) → row label PriceCharting uses on the card page.
# PriceCharting has authoritative grader-specific rows only at grade 10
# (PSA 10, CGC 10, BGS 10, SGC 10, plus rare BGS 10 Black / CGC 10 Pristine).
# For sub-10 grades they expose a generic cross-service "Grade N" row;
# we map all sub-10 grades to that, accepting some loss of precision.
GRADE_TO_PC_ROW = {
    ("PSA", 10):    "PSA 10",
    ("CGC", 10):    "CGC 10",
    ("BGS", 10):    "BGS 10",
    ("SGC", 10):    "SGC 10",
    ("PSA", 9.5):   "Grade 9.5",
    ("PSA", 9):     "Grade 9",
    ("PSA", 8):     "Grade 8",
    ("PSA", 7):     "Grade 7",
    ("CGC", 9.5):   "Grade 9.5",
    ("CGC", 9):     "Grade 9",
    ("CGC", 8):     "Grade 8",
    ("BGS", 9.5):   "Grade 9.5",
    ("BGS", 9):     "Grade 9",
    ("BGS", 8):     "Grade 8",
    ("SGC", 9.5):   "Grade 9.5",
    ("SGC", 9):     "Grade 9",
    ("SGC", 8):     "Grade 8",
}

SEALED_PRODUCT_SLUGS: dict[str, str] = {
    "booster_box":  "booster-box",
    "etb":          "elite-trainer-box",
    "booster_pack": "booster-pack",
    "tin":          "tin",
    "bundle":       "booster-bundle",
}

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0.0.0 Safari/537.36")

# <tr><td>LABEL</td><td class="price js-price">$N.NN</td></tr>
_PRICE_ROW_RE = re.compile(
    r'<tr>\s*<td>([^<]+)</td>\s*<td[^>]*class="[^"]*\bprice\b[^"]*\bjs-price\b[^"]*"[^>]*>\s*\$?([0-9.,\-]+)\s*</td>',
    re.IGNORECASE,
)

# Every PriceCharting product page embeds its price-history graph data inline:
#   VGPC.chart_data = {"used": [[<ms timestamp>, <price in cents>], ...], "graded": [...], ...}
# "used" is PriceCharting's loose/ungraded series — it lines up with the
# "Ungraded" row `lookup_raw_price` reads from the price table (verified:
# Venusaur ex 198/165 chart's latest "used" point ($120.24) ≈ live
# "Ungraded" price ($120.23)). Spans ~3 years at roughly monthly granularity,
# far more history than this app has collected on its own (~3 weeks).
_CHART_DATA_RE = re.compile(r'VGPC\.chart_data\s*=\s*(\{.*?\});', re.DOTALL)


@dataclass
class PriceChartingResult:
    url: str
    grade_label: str           # e.g. "PSA 10" or "Grade 9.5"
    price_usd: Optional[float]
    all_prices: dict[str, float]   # full price table, label → USD
    cached: bool


# ---------------------------------------------------------------------------
# Slug + URL construction
# ---------------------------------------------------------------------------

def _slug(s: str) -> str:
    s = s.lower()
    s = re.sub(r"['.,:!()&]", "", s)
    s = re.sub(r"\s+", "-", s.strip())
    s = re.sub(r"-+", "-", s)
    return s.strip("-")


def _normalize_card_number(num: str) -> str:
    """For the URL we want '174', '25', or 'TG11' — drop the /total and
    leading zeros for purely-numeric numbers."""
    head = num.split("/")[0].strip()
    if head.isdigit():
        return str(int(head))
    return head


def _candidate_urls(name: str, set_name: str, card_number: str,
                    language: str = "english",
                    variant: Optional[str] = None) -> list[str]:
    """Generate plausible PriceCharting URLs to try in order.

    PriceCharting's slug scheme is annoyingly card-specific:
      - Some cards have ONE page that covers all prints (Fossil Gengar at
        `gengar-5` has Unlimited Holo data; there's no `gengar-holo-5`).
      - Other cards have SEPARATE pages per print: Team Rocket Dark Dragonite
        has `dark-dragonite-5` (non-holo) AND `dark-dragonite-holo-5` (Holo
        Unlimited, the one whose PSA 9 trades at ~$445).
      - 1st Edition is usually a separate page: `dark-dragonite-1st-edition-5`.

    We can't predict which form a given card uses, so we generate the
    likely candidates and try them in order. The fetcher skips any URL
    that redirects to `/search-products` (PriceCharting's "no such page"
    behaviour), so dead variants drop out automatically.
    """
    name_slug = _slug(name)
    num = _normalize_card_number(card_number)

    raw_set = (set_name or "").strip()
    set_clean = re.sub(r"^(SV|S&V|Scarlet\s*&\s*Violet|SWSH|Sword\s*&\s*Shield)[:\s]+",
                        "", raw_set, flags=re.IGNORECASE).strip()
    set_no_tg = re.sub(r"\s+Trainer\s+Gallery\s*$", "", set_clean, flags=re.IGNORECASE).strip()

    base_slugs = list(dict.fromkeys([_slug(s) for s in (set_clean, set_no_tg, raw_set) if s]))

    # ----- Detect variant flags from the user/LLM-provided variant string ----
    v = (variant or "").strip().lower()
    is_holo       = "holo" in v and "reverse" not in v
    is_first_ed   = "1st" in v or "first edition" in v
    is_shadowless = "shadowless" in v
    is_reverse    = "reverse" in v

    # ----- Name-slug qualifiers: try most specific first ----------------------
    # The qualifier is inserted between the card name and the number, e.g.
    # `dark-dragonite-holo-5` or `dark-dragonite-1st-edition-5`. We always
    # include the plain `name-num` as a final fallback because some cards
    # (Fossil Gengar) put all data on the default page.
    name_qualifiers: list[str] = []
    if is_first_ed and is_holo:
        name_qualifiers += ["1st-edition-holo", "holo-1st-edition", "1st-edition", "holo"]
    elif is_first_ed:
        name_qualifiers += ["1st-edition"]
    elif is_holo:
        name_qualifiers += ["holo"]
    elif is_shadowless:
        name_qualifiers += ["shadowless"]
    elif is_reverse:
        name_qualifiers += ["reverse-holo"]
    name_qualifiers.append("")    # plain name-num — final fallback

    # ----- Set-slug variants: some cards use `set-1st-edition` instead --------
    set_variant_suffix = ""
    if v in ("1st edition", "first edition"):
        set_variant_suffix = "-1st-edition"
    elif v == "shadowless":
        set_variant_suffix = "-shadowless"

    set_variants: list[str] = []
    if set_variant_suffix:
        set_variants.extend(s + set_variant_suffix for s in base_slugs)
    set_variants.extend(base_slugs)
    seen = set()
    set_variants = [s for s in set_variants if not (s in seen or seen.add(s))]

    jp_prefix = "japanese-" if language.lower() == "japanese" else ""

    # Build URLs: outer loop set variants, inner loop name qualifiers
    urls: list[str] = []
    for set_slug in set_variants:
        for qual in name_qualifiers:
            if qual:
                url = f"{PC_BASE}/game/pokemon-{jp_prefix}{set_slug}/{name_slug}-{qual}-{num}"
            else:
                url = f"{PC_BASE}/game/pokemon-{jp_prefix}{set_slug}/{name_slug}-{num}"
            urls.append(url)
    # Final dedupe preserving order
    out_seen = set()
    return [u for u in urls if not (u in out_seen or out_seen.add(u))]


# ---------------------------------------------------------------------------
# Cache (SQLite, 24h TTL)
# ---------------------------------------------------------------------------

def _init_cache() -> None:
    conn = sqlite3.connect(str(CACHE_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pc_cache (
            url         TEXT PRIMARY KEY,
            prices_json TEXT NOT NULL,
            ts          REAL NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def _cache_get(url: str) -> Optional[dict[str, float]]:
    _init_cache()
    conn = sqlite3.connect(str(CACHE_DB))
    row = conn.execute(
        "SELECT prices_json, ts FROM pc_cache WHERE url = ?", (url,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    if time.time() - row[1] > CACHE_TTL_SECONDS:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return None


def _cache_set(url: str, prices: dict[str, float]) -> None:
    _init_cache()
    conn = sqlite3.connect(str(CACHE_DB))
    conn.execute(
        "INSERT OR REPLACE INTO pc_cache (url, prices_json, ts) VALUES (?, ?, ?)",
        (url, json.dumps(prices), time.time()),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------

def _parse_price_table(html: str) -> dict[str, float]:
    """Extract the per-grade price table from a PriceCharting card page."""
    out: dict[str, float] = {}
    for label, value in _PRICE_ROW_RE.findall(html):
        label = label.strip()
        value = value.strip()
        if value in ("", "-"):
            continue
        try:
            out[label] = float(value.replace(",", ""))
        except ValueError:
            continue
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def lookup_raw_price(name: str, set_name: str, card_number: str,
                            language: str = "english",
                            variant: Optional[str] = None) -> Optional[PriceChartingResult]:
    """Pull the 'Ungraded' price from PriceCharting for this card.

    `variant` matters for OLD prints: 1st Edition and Shadowless live on
    separate PC pages and trade at different prices. Pass it through so
    the URL builder prefers the right slug.
    """
    urls = _candidate_urls(name, set_name, card_number, language, variant=variant)
    if not urls:
        return None
    prices_table = await _fetch_prices_with_cache(urls)
    if not prices_table:
        return None
    table, url = prices_table
    return PriceChartingResult(
        url=url, grade_label="Ungraded",
        price_usd=table.get("Ungraded"),
        all_prices=table, cached=False,
    )


async def fetch_chart_history(name: str, set_name: str, card_number: str,
                               language: str = "english",
                               variant: Optional[str] = None,
                               series: str = "used") -> Optional[tuple[list[tuple[int, float]], str]]:
    """Scrape PriceCharting's embedded `VGPC.chart_data` for this card.

    Returns `([(timestamp_ms, price_usd), ...] sorted ascending, url)`, or
    None. `series` selects which PriceCharting line to read — "used" is the
    raw/ungraded line (see `_CHART_DATA_RE` for the verified mapping).

    Bypasses the price-table cache: this hits the same product page but reads
    a different embedded payload, and the result (a multi-year series) is
    meant to be persisted into `price_history` rather than cached here.
    """
    urls = _candidate_urls(name, set_name, card_number, language, variant=variant)
    if not urls:
        return None
    async with httpx.AsyncClient(timeout=15.0,
                                  headers={"User-Agent": USER_AGENT}) as client:
        for url in urls:
            try:
                r = await client.get(url, follow_redirects=True)
            except httpx.HTTPError as e:
                log.warning("PriceCharting chart fetch failed %s: %s", url, e)
                continue
            if r.status_code != 200:
                continue
            final_path = str(r.url.path)
            if final_path.startswith("/search-products") or final_path == "/":
                continue
            m = _CHART_DATA_RE.search(r.text)
            if not m:
                continue
            try:
                data = json.loads(m.group(1))
            except (json.JSONDecodeError, ValueError):
                continue
            points = data.get(series) or []
            cleaned = sorted((int(t), v / 100.0) for t, v in points if v)
            if not cleaned:
                continue
            return cleaned, url
    return None


async def _fetch_prices_with_cache(urls: list[str]) -> Optional[tuple[dict, str]]:
    """Walk URL candidates; return (price_table, url) for the first hit.
    Cache hits return immediately; cache misses fetch and cache."""
    async with httpx.AsyncClient(timeout=15.0,
                                  headers={"User-Agent": USER_AGENT}) as client:
        for url in urls:
            cached = _cache_get(url)
            if cached is not None:
                return cached, url
            try:
                r = await client.get(url, follow_redirects=True)
            except httpx.HTTPError as e:
                log.warning("PriceCharting fetch failed %s: %s", url, e)
                continue
            if r.status_code != 200:
                continue
            final_path = str(r.url.path)
            if final_path.startswith("/search-products") or final_path == "/":
                continue
            prices = _parse_price_table(r.text)
            if not prices:
                continue
            _cache_set(url, prices)
            return prices, url
    return None


async def lookup_graded_price(name: str, set_name: str, card_number: str,
                               language: str, grade_company: str,
                               grade: float,
                               variant: Optional[str] = None) -> Optional[PriceChartingResult]:
    """Return a PriceChartingResult for the requested grade, or None if the
    card / grade isn't in PriceCharting's catalogue."""
    row_label = GRADE_TO_PC_ROW.get((grade_company.upper(), float(grade)))
    if not row_label:
        return None

    urls = _candidate_urls(name, set_name, card_number, language, variant=variant)
    if not urls:
        return None

    async with httpx.AsyncClient(timeout=15.0,
                                  headers={"User-Agent": USER_AGENT}) as client:
        for url in urls:
            # Cache hit?
            cached = _cache_get(url)
            if cached is not None:
                price = cached.get(row_label)
                return PriceChartingResult(
                    url=url, grade_label=row_label,
                    price_usd=price, all_prices=cached, cached=True,
                )
            # Miss — fetch fresh
            try:
                r = await client.get(url, follow_redirects=True)
            except httpx.HTTPError as e:
                log.warning("PriceCharting fetch failed %s: %s", url, e)
                continue
            if r.status_code == 404:
                continue
            if r.status_code != 200:
                log.warning("PriceCharting returned %s for %s", r.status_code, url)
                continue

            # Card-not-found behaviour: PC silently 200-redirects to a
            # search-results page rather than returning a real 404.
            final_path = str(r.url.path)
            if final_path.startswith("/search-products") or final_path == "/":
                log.info("PriceCharting redirected %s → %s (card not in catalogue)",
                         url, r.url)
                continue

            prices = _parse_price_table(r.text)
            if not prices:
                continue
            _cache_set(url, prices)
            return PriceChartingResult(
                url=url, grade_label=row_label,
                price_usd=prices.get(row_label),
                all_prices=prices, cached=False,
            )

    return None


async def lookup_sealed_price(
    name: str,
    set_name: str,
    product_type: str,
    language: str = "english",
) -> "PriceChartingResult | None":
    """Return a PriceChartingResult for a sealed product (booster box, ETB, etc.).

    Builds a PriceCharting URL from the set slug and the product-type slug,
    checks the 24h cache, fetches if needed, and parses the price table.
    Looks for a "Sealed" row first; falls back to "Ungraded" if absent.

    Returns None if `product_type` is not in SEALED_PRODUCT_SLUGS or if the
    product page isn't in PriceCharting's catalogue.
    """
    product_slug = SEALED_PRODUCT_SLUGS.get(product_type)
    if product_slug is None:
        return None

    set_slug = _slug(set_name or name)
    if language.lower() == "japanese":
        set_slug = set_slug + "-japanese"

    url = f"{PC_BASE}/game/pokemon-{set_slug}/{product_slug}"

    # Cache hit?
    cached = _cache_get(url)
    if cached is not None:
        price_label = "Sealed" if "Sealed" in cached else "Ungraded"
        return PriceChartingResult(
            url=url,
            grade_label=price_label,
            price_usd=cached.get(price_label),
            all_prices=cached,
            cached=True,
        )

    # Cache miss — fetch
    try:
        async with httpx.AsyncClient(timeout=15.0,
                                      headers={"User-Agent": USER_AGENT}) as client:
            r = await client.get(url, follow_redirects=True)
    except httpx.HTTPError as e:
        log.warning("PriceCharting sealed fetch failed %s: %s", url, e)
        return None

    if r.status_code != 200:
        log.warning("PriceCharting sealed returned %s for %s", r.status_code, url)
        return None

    final_path = str(r.url.path)
    if final_path.startswith("/search-products") or final_path == "/":
        log.info("PriceCharting sealed redirected %s → %s (not in catalogue)", url, r.url)
        return None

    prices = _parse_price_table(r.text)
    if not prices:
        return None

    _cache_set(url, prices)

    price_label = "Sealed" if "Sealed" in prices else "Ungraded"
    return PriceChartingResult(
        url=url,
        grade_label=price_label,
        price_usd=prices.get(price_label),
        all_prices=prices,
        cached=False,
    )
