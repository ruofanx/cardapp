"""
Shared raw-card "best available, sold-aware" price resolver.

Used by both /api/refresh-price (on-demand) and the daily refresh job, so the
blend between catalogue baselines (TCGplayer/Cardmarket/eBay Browse) and
PriceCharting's sold-comp-derived "Ungraded" price only needs to be
implemented and tested once.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import card_lookup
import ebay_browse_api
import ebay_lookup
import pricecharting_lookup

log = logging.getLogger(__name__)

# If PriceCharting's sold-comp-derived "Ungraded" price diverges from the
# catalogue baseline by more than this fraction, prefer PriceCharting — it's
# a closer "what is this actually selling for" signal than a catalogue index.
RAW_PRICE_DIVERGENCE_THRESHOLD = 0.15


@dataclass
class RawPriceResult:
    nm_price: Optional[float]
    baseline_label: str
    extra_note: Optional[str]


async def _baseline_price(
    name: str, set_name: str, card_number: str,
    language: str, variant: Optional[str],
) -> RawPriceResult:
    """Today's catalogue-based cascade, extracted as-is from /api/refresh-price."""
    nm_price: Optional[float] = None
    baseline_label = "TCGplayer (EN)"
    extra_note: Optional[str] = None

    # PRIMARY for JP cards: eBay Browse API median of relevant active
    # listings. Cardmarket EUR data is stale for newer JP sets.
    if name and language.lower() == "japanese":
        try:
            br = await ebay_browse_api.median_relevant_price(
                name, set_name, card_number, language="japanese",
            )
        except Exception as e:
            log.warning("eBay Browse median lookup failed: %s", e)
            br = None
        if br and br["median_usd"]:
            nm_price = float(br["median_usd"])
            baseline_label = (
                f"eBay Browse median (JP, n={br['sample_size']} of "
                f"{br['raw_sample_size']}, range "
                f"${br['low_usd']:.2f}-${br['high_usd']:.2f})"
            )
            extra_note = (
                f"Trimmed-median of {br['sample_size']} relevant active "
                f"listings from eBay (query: {br['query']!r}). Cardmarket "
                f"EUR is often stale for newer JP sets."
            )

    if nm_price is None:
        base = await card_lookup.lookup_card(
            name, set_name, card_number, language=language, variant=variant,
        )
        if base and base.market_price:
            nm_price = float(base.market_price)
            variant_tag = f" / {variant}" if variant else ""
            baseline_label = (
                "Cardmarket EUR (JP)" if base.source == "cardmarket-jp"
                else f"TCGplayer (EN{variant_tag})"
            )

    # Last-ditch second opinion for JP — eBay sold listings.
    if nm_price is None and language.lower() == "japanese":
        try:
            ebay = await ebay_lookup.lookup_raw_price(
                name, set_name, card_number, language="japanese", condition="NM",
            )
        except Exception as e:
            log.warning("eBay lookup failed for JP card: %s", e)
            ebay = None
        if ebay and ebay.median_usd:
            nm_price = float(ebay.median_usd)
            baseline_label = "eBay sold (JP-keyword)"
            extra_note = (f"eBay sold-median n={ebay.sample_size}/{ebay.raw_sample_size}, "
                          f"{ebay.period_days}d window")

    return RawPriceResult(nm_price=nm_price, baseline_label=baseline_label, extra_note=extra_note)


async def resolve_raw_price(
    name: str, set_name: str, card_number: str,
    language: str = "english", variant: Optional[str] = None,
) -> RawPriceResult:
    """Best-available NM price for a raw card, blending the catalogue
    baseline with PriceCharting's sold-comp-derived "Ungraded" price.

    PriceCharting's Ungraded price is itself derived from aggregated recent
    sold comps, so when it diverges meaningfully from the catalogue baseline
    it's treated as the more accurate "recent sold" signal.
    """
    baseline = await _baseline_price(name, set_name, card_number, language, variant)

    try:
        pc_raw = await pricecharting_lookup.lookup_raw_price(
            name, set_name, card_number, language=language, variant=variant,
        )
    except Exception as e:
        log.warning("PriceCharting raw lookup failed: %s", e)
        pc_raw = None
    pc_price = float(pc_raw.price_usd) if pc_raw and pc_raw.price_usd else None

    if pc_price is None:
        return baseline

    if baseline.nm_price is None:
        return RawPriceResult(
            nm_price=pc_price,
            baseline_label="PriceCharting Ungraded (sold-based)",
            extra_note=None,
        )

    divergence = abs(pc_price - baseline.nm_price) / baseline.nm_price
    if divergence <= RAW_PRICE_DIVERGENCE_THRESHOLD:
        return baseline

    return RawPriceResult(
        nm_price=pc_price,
        baseline_label="PriceCharting Ungraded (sold-based)",
        extra_note=(
            f"{baseline.baseline_label} was ${baseline.nm_price:.2f} "
            f"(diverged {divergence:.0%}) — using PriceCharting sold-based price."
        ),
    )
