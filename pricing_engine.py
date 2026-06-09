"""
Pokemon Card Pricing Engine — prototype.

Designed to be source-agnostic: each price source is a Fetcher that returns
raw sale records, and the engine handles filtering, outlier removal, and
median calculation uniformly.
"""
from __future__ import annotations

import re
import statistics
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from html import unescape
from typing import Literal, Optional
from urllib.parse import urlencode


CardLanguage = Literal["english", "japanese"]
CardCondition = Literal["NM", "LP", "MP", "HP", "DMG"]
GradingCompany = Literal["PSA", "BGS", "CGC", "SGC", "ARS"]


@dataclass
class CardIdentity:
    name: str
    set_name: str
    card_number: str
    language: CardLanguage
    variant: Optional[str] = None

    def search_terms(self) -> list[str]:
        parts = [self.name, self.set_name, self.card_number]
        if self.variant:
            parts.append(self.variant)
        if self.language == "japanese":
            parts.append("Japanese")
        return [p for p in parts if p]


@dataclass
class CardQuery:
    card: CardIdentity
    is_graded: bool = False
    condition: Optional[CardCondition] = None
    grade_company: Optional[GradingCompany] = None
    grade: Optional[float] = None

    def __post_init__(self):
        if self.is_graded:
            assert self.grade_company and self.grade, "graded queries need company + grade"
        else:
            assert self.condition, "raw queries need condition"


PRODUCT_TYPE_SEARCH_TERMS: dict[str, str] = {
    "booster_pack": "Booster Pack",
    "booster_box":  "Booster Box",
    "etb":          "Elite Trainer Box",
    "tin":          "Tin",
    "bundle":       "Bundle",
}


@dataclass
class SealedProductQuery:
    name: str
    set_name: str
    product_type: str   # one of PRODUCT_TYPE_SEARCH_TERMS keys
    language: str = "english"


@dataclass
class SaleRecord:
    price_usd: float
    sold_date: datetime
    title: str
    url: Optional[str] = None
    source: str = ""


@dataclass
class PriceQuote:
    source: str
    median_usd: float
    sample_size: int
    raw_sample_size: int
    period_days: int
    low_usd: float
    high_usd: float
    sales: list[SaleRecord] = field(default_factory=list)

    @property
    def is_bulk(self) -> bool:
        return self.median_usd < 5.0


@dataclass
class CardPriceReport:
    english: Optional[CardQuery]
    japanese: Optional[CardQuery]
    en_quotes: list[PriceQuote] = field(default_factory=list)
    jp_quotes: list[PriceQuote] = field(default_factory=list)


_CONDITION_KEYWORDS = {
    "LP":  '(LP,"Lightly Played","Light Play")',
    "MP":  '(MP,"Moderately Played","Moderate Play","Played")',
    "HP":  '(HP,"Heavily Played","Heavy Play")',
    "DMG": '(DMG,Damaged,Poor)',
}


def build_ebay_query_string(query: CardQuery) -> str:
    parts = list(query.card.search_terms())
    if query.is_graded:
        parts.append(f"{query.grade_company} {query.grade}")
    else:
        for kw in ("PSA", "BGS", "CGC", "SGC", "graded", "slab", "slabbed"):
            parts.append(f"-{kw}")
        for kw in ("proxy", "custom", "lot", "bulk"):
            parts.append(f"-{kw}")
        cond_expr = _CONDITION_KEYWORDS.get(query.condition or "")
        if cond_expr:
            parts.append(cond_expr)
    return " ".join(parts)


def build_ebay_sold_url(query: CardQuery) -> str:
    keywords = build_ebay_query_string(query)
    params = {
        "_nkw": keywords,
        "LH_Sold": "1",
        "LH_Complete": "1",
        "_ipg": "240",
        "_sop": "13",
    }
    return f"https://www.ebay.com/sch/i.html?{urlencode(params)}"


def build_ebay_sealed_url(query: SealedProductQuery) -> str:
    """Build eBay sold-listings URL for a sealed Pokemon product.

    Uses set_name as the primary search term. Excludes opened/resealed
    listings. For non-pack types also excludes loose individual packs.
    """
    product_term = PRODUCT_TYPE_SEARCH_TERMS.get(query.product_type, "")
    parts: list[str] = []
    if query.set_name:
        parts.append(query.set_name)
    elif query.name:
        parts.append(query.name)
    if product_term:
        parts.append(product_term)
    parts.append("sealed")
    parts += ["-opened", "-resealed", "-empty"]
    if query.product_type != "booster_pack":
        parts.append("-pack")   # avoid individual packs when searching for boxes/ETBs/tins
    if query.language.lower() == "japanese":
        parts.append("Japanese")
    keywords = " ".join(parts)
    params = {
        "_nkw": keywords,
        "LH_Sold": "1",
        "LH_Complete": "1",
        "_ipg": "240",
        "_sop": "13",
    }
    return f"https://www.ebay.com/sch/i.html?{urlencode(params)}"


def is_relevant_sealed_title(title: str, query: SealedProductQuery) -> bool:
    """Return True if this eBay listing title looks like a genuine sealed-product sale."""
    t = title.lower()
    if any(j in t for j in ("opened", "resealed", "empty box", "factory seconds")):
        return False
    if re.search(r"\b(lot of|x[2-9]|[2-9]x|\d+ packs)\b", t):
        return False
    search_text = query.set_name or query.name
    tokens = [w.lower() for w in re.findall(r"\w+", search_text) if len(w) > 2]
    if tokens and not any(tok in t for tok in tokens):
        return False
    return True


class Fetcher(ABC):
    source_name: str

    @abstractmethod
    def fetch(self, query: CardQuery, period_days: int = 30) -> list[SaleRecord]: ...


class EbayHtmlFetcher(Fetcher):
    source_name = "ebay-us"

    def __init__(self, fetch_url):
        self.fetch_url = fetch_url

    def fetch(self, query: CardQuery, period_days: int = 30) -> list[SaleRecord]:
        url = build_ebay_sold_url(query)
        html = self.fetch_url(url)
        return parse_ebay_sold_html(html, period_days=period_days)


_PRICE_RE = re.compile(r"\$\s*([\d,]+\.?\d*)")

# Date format eBay uses in sold-listing pages: "Sold Jan  5, 2024" (may have
# extra spaces). We match it wherever it appears in an item block.
_SOLD_DATE_RE = re.compile(r'Sold\s+([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})')

# Price: try the current class name first, then common historical names, then
# any bare dollar amount as a last resort.
_ITEM_PRICE_RE = re.compile(
    r'class="[^"]*s-item__price[^"]*"[^>]*>[^$<]*\$([\d,]+\.\d{2})'          # current
    r'|class="[^"]*s-card__price[^"]*"[^>]*>[^$<]*\$([\d,]+\.\d{2})'         # legacy
    r'|class="[^"]*notranslate[^"]*"[^>]*>[^$<]*\$([\d,]+\.\d{2})',           # alt
)

# Title: role="heading" is the most stable anchor; fall back to known classes.
_ITEM_TITLE_RE = re.compile(
    r'role="heading"[^>]*>([^<]{8,})</span>'
    r'|class="[^"]*s-item__title[^"]*"[^>]*>[^<]*<span[^>]*>([^<]{8,})</span>'
    r'|class="[^"]*su-styled-text[^"]*"[^>]*>([^<]{8,})</span>',
)

# Item URL — href inside the item block's primary link.
_ITEM_URL_RE = re.compile(r'href="(https://www\.ebay\.com/itm/[^"]+)"')


def parse_ebay_sold_html(html: str, period_days: int = 30) -> list[SaleRecord]:
    """Parse eBay sold-listings HTML into SaleRecord list.

    Splits on per-item boundaries so each field is extracted independently
    from a small block — avoids fragile cross-item dot-star matches and
    survives minor eBay HTML changes more gracefully.
    """
    cutoff = datetime.now() - timedelta(days=period_days)
    results: list[SaleRecord] = []

    # Split on the start of each result item.  eBay wraps each hit in a <li>
    # with class "s-item"; splitting on that boundary keeps extraction local.
    # The first chunk is page chrome before the first item — skip it.
    blocks = re.split(r'<li\b[^>]*\bclass="[^"]*s-item\b', html)

    for block in blocks[1:]:
        # --- Sold date (required) ---
        dm = _SOLD_DATE_RE.search(block)
        if not dm:
            continue
        try:
            sold_date = datetime.strptime(dm.group(1).strip(), "%b %d, %Y")
        except ValueError:
            continue
        if sold_date < cutoff:
            continue

        # --- Price (required) ---
        pm = _ITEM_PRICE_RE.search(block)
        if not pm:
            # last-resort: first bare $ amount in the block
            fm = re.search(r'\$([\d,]+\.\d{2})', block)
            if not fm:
                continue
            price_str = fm.group(1)
        else:
            price_str = next(g for g in pm.groups() if g is not None)
        try:
            price = float(price_str.replace(",", ""))
        except ValueError:
            continue

        # --- Title (best-effort; empty string if not found) ---
        tm = _ITEM_TITLE_RE.search(block)
        if tm:
            title = unescape(next(g for g in tm.groups() if g is not None)).strip()
        else:
            title = ""
        if title.lower().startswith("shop on ebay"):
            continue

        # --- URL (optional) ---
        um = _ITEM_URL_RE.search(block)
        url = um.group(1).split("?")[0] if um else None  # strip tracking params

        results.append(SaleRecord(
            price_usd=price, sold_date=sold_date, title=title,
            url=url, source="ebay-us",
        ))

    return results


def is_relevant_title(title: str, query: CardQuery) -> bool:
    t = title.lower()
    junk = ["proxy", "custom", "fake", "replica", "fan made", "metal card",
            "jumbo", "oversized", "playmat", "sleeve", "binder", "pin",
            "code card", "online code"]
    if any(j in t for j in junk):
        return False
    if re.search(r"\b(lot of|bundle|x\d+|\d+\s*cards?\b)", t) and not re.search(r"\b1\s*card\b", t):
        if any(x in t for x in [" lot of ", " bundle ", " cards "]) and "1 card" not in t:
            return False
    name_tokens = [w.lower() for w in re.findall(r"\w+", query.card.name) if len(w) > 2]
    if name_tokens and not any(tok in t for tok in name_tokens):
        return False
    if not query.is_graded:
        if re.search(r"\b(psa|bgs|cgc|sgc)\b\s*\d", t):
            return False
        if "graded" in t or "slab" in t:
            return False
    if query.is_graded:
        if query.grade_company and query.grade_company.lower() not in t:
            return False
        if query.grade and not re.search(rf"\b{re.escape(str(query.grade))}\b", t):
            return False
    return True


def aggregate_sales(sales, query, period_days=30, trim_pct=0.10):
    relevant = [s for s in sales if is_relevant_title(s.title, query)]
    if not relevant:
        return None
    prices = sorted(s.price_usd for s in relevant)
    n = len(prices)
    trim = max(1, int(n * trim_pct)) if n >= 5 else 0
    trimmed = prices[trim:n - trim] if trim else prices
    if not trimmed:
        return None
    return PriceQuote(
        source=relevant[0].source or "unknown",
        median_usd=statistics.median(trimmed),
        sample_size=len(trimmed),
        raw_sample_size=n,
        period_days=period_days,
        low_usd=min(trimmed), high_usd=max(trimmed),
        sales=relevant,
    )


def price_card_side_by_side(en_query, jp_query, fetchers, period_days=30, max_workers=8):
    report = CardPriceReport(english=en_query, japanese=jp_query)
    jobs = []
    for fetcher in fetchers:
        if en_query: jobs.append(("en", fetcher, en_query))
        if jp_query: jobs.append(("jp", fetcher, jp_query))
    if not jobs:
        return report
    workers = min(max_workers, len(jobs))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(f.fetch, q, period_days): (lang, f, q) for lang, f, q in jobs}
        for future in as_completed(futures):
            lang, _f, q = futures[future]
            try:
                sales = future.result()
            except Exception:
                continue
            quote = aggregate_sales(sales, q, period_days=period_days)
            if not quote:
                continue
            (report.en_quotes if lang == "en" else report.jp_quotes).append(quote)
    report.en_quotes.sort(key=lambda q: q.source)
    report.jp_quotes.sort(key=lambda q: q.source)
    return report
