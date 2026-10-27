"""
Shared raw-card "best available, sold-aware" price resolver.

Pricing model (sold anchor + listing trend):

  sold_base  = mean of the sold-based signals available:
               PriceCharting "Ungraded" (aggregated eBay sold comps) and
               TCGplayer market price (EN only — recent-sale based)
  ask_median = eBay Browse API active-listing median, NM-condition titles
               only, discounted by BROWSE_HAIRCUT to approximate clearing price

  trend      = (ask_median × (1 - BROWSE_HAIRCUT) / sold_base) - 1
  trend_adj  = clamp(trend, -TREND_CAP, +TREND_CAP) × TREND_WEIGHT
  price      = sold_base × (1 + trend_adj)

Asks sit above what cards actually sell for, so they are only a damped
nudge on the sold anchor, never the price itself. TREND_CAP / TREND_WEIGHT
keep a stale sold price or a thin Browse sample from swinging the result.

If sold data is unavailable, Browse-only falls back to ask_median minus
BROWSE_HAIRCUT (active listings typically clear ~12% below ask).

All three signals are fetched in parallel for lower latency.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

import card_lookup
import ebay_browse_api
import ebay_lookup
import pricecharting_lookup

log = logging.getLogger(__name__)

TREND_CAP = 0.15       # max ±15% trend signal from sold base
TREND_WEIGHT = 0.5     # fraction of the capped trend applied (asks ≠ sales)
BROWSE_HAIRCUT = 0.12  # active listings typically run ~12% above clearing price


@dataclass
class RawPriceResult:
    nm_price: Optional[float]
    baseline_label: str
    extra_note: Optional[str]


# ---------------------------------------------------------------------------
# Individual signal fetchers
# ---------------------------------------------------------------------------

async def _get_pc_price(
    name: str, set_name: str, card_number: str,
    language: str, variant: Optional[str],
) -> Optional[float]:
    """PriceCharting 'Ungraded' — aggregated eBay sold comps, most reliable signal."""
    try:
        pc = await pricecharting_lookup.lookup_raw_price(
            name, set_name, card_number, language=language, variant=variant,
        )
    except Exception as e:
        log.warning("PriceCharting raw lookup failed: %s", e)
        return None
    return float(pc.price_usd) if pc and pc.price_usd else None


async def _get_catalog_price(
    name: str, set_name: str, card_number: str,
    language: str, variant: Optional[str],
) -> tuple[Optional[float], Optional[str]]:
    """TCGplayer (EN, sold-based) or Cardmarket EUR (JP, listing-based) price."""
    try:
        base = await card_lookup.lookup_card(
            name, set_name, card_number, language=language, variant=variant,
        )
    except Exception as e:
        log.warning("Catalog lookup failed: %s", e)
        return None, None
    if not base or not base.market_price:
        return None, None
    is_cardmarket_jp = getattr(base, "source", "") == "cardmarket-jp"
    # TCGplayer EN prices don't reflect a non-English market — skip for any
    # non-EN card. (Not just JP: `card_lookup.lookup_card` has no Chinese-
    # specific catalog route, so a "chinese" lookup falls straight into the
    # EN Pokemon TCG API and can spuriously match an EN print that happens to
    # share the same card number — e.g. Chinese-151 Illustration Rare Pikachu
    # #173 vs the unrelated EN Scarlet & Violet 151 Pikachu #173.)
    if language.lower() != "english" and not is_cardmarket_jp:
        return None, None
    label = "Cardmarket EUR (JP)" if is_cardmarket_jp else "TCGplayer market (EN)"
    return float(base.market_price), label


async def _get_browse_price(
    name: str, set_name: str, card_number: str, language: str,
    variant: Optional[str] = None,
) -> Optional[dict]:
    """eBay Browse API active-listing median — trend/direction signal.

    For JP cards, retries with a wider query when the primary result looks wrong
    (e.g. SIR-range card numbers that collide with cheap card numbers in JP listings).
    Returns dict with 'price', 'label', 'query' or None.
    """
    try:
        br = await ebay_browse_api.median_relevant_price(
            name, set_name, card_number, language=language, variant=variant,
            nm_only=True,
        )
    except Exception as e:
        log.warning("eBay Browse median lookup failed: %s", e)
        return None

    if language.lower() == "japanese":
        cn_digits = (card_number or "").split("/")[0].strip()
        is_sir_range = cn_digits.isdigit() and int(cn_digits) > 130
        primary_looks_wrong = (
            br is None
            or (is_sir_range and br["median_usd"] < 50)
            or (br is not None and br["sample_size"] < 3)
        )
        if primary_looks_wrong:
            try:
                br = await ebay_browse_api.median_relevant_price(
                    name, set_name=None, card_number=None,
                    language="japanese", sample_size=20, nm_only=True,
                )
            except Exception as e:
                log.warning("eBay Browse JP fallback failed: %s", e)
                br = None

    if not br or not br.get("median_usd"):
        return None
    return {
        "price": float(br["median_usd"]),
        "label": (
            f"eBay Browse n={br['sample_size']}/{br.get('raw_sample_size', '?')}, "
            f"${br['low_usd']:.2f}–${br['high_usd']:.2f}"
        ),
        "query": br.get("query", ""),
    }


# ---------------------------------------------------------------------------
# Trend blend
# ---------------------------------------------------------------------------

def _apply_trend(sold_base: float, sold_label: str, browse: dict) -> RawPriceResult:
    """Blend a sold-comp anchor with an active-listing trend signal."""
    ask = browse["price"]
    ask_clearing = ask * (1 - BROWSE_HAIRCUT)
    trend = (ask_clearing / sold_base) - 1
    trend_adj = max(-TREND_CAP, min(TREND_CAP, trend)) * TREND_WEIGHT
    market = sold_base * (1 + trend_adj)
    direction = (
        "rising" if trend_adj > 0.05
        else "softening" if trend_adj < -0.05
        else "stable"
    )
    return RawPriceResult(
        nm_price=round(market, 2),
        baseline_label=f"{sold_label} + eBay trend ({direction})",
        extra_note=(
            f"Sold anchor ({sold_label}): ${sold_base:.2f}. "
            f"Active NM listings ({browse['label']}): ${ask:.2f} "
            f"(≈${ask_clearing:.2f} after {BROWSE_HAIRCUT:.0%} ask-to-sale discount). "
            f"Trend: {trend:+.0%} → adj {trend_adj:+.1%} ({direction}). "
            f"Market price: ${market:.2f}."
        ),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def resolve_raw_price(
    name: str, set_name: str, card_number: str,
    language: str = "english", variant: Optional[str] = None,
) -> RawPriceResult:
    """Best-available NM price for a raw card.

    Fetches PriceCharting, catalog (TCGplayer/Cardmarket), and eBay Browse in
    parallel, then applies the sold-anchor + trend-adjustment model.
    Falls back gracefully when signals are missing.
    """
    pc_price, (catalog_price, catalog_label), browse = await asyncio.gather(
        _get_pc_price(name, set_name, card_number, language, variant),
        _get_catalog_price(name, set_name, card_number, language, variant),
        _get_browse_price(name, set_name, card_number, language, variant),
    )

    # --- Sold base -----------------------------------------------------------
    # PriceCharting (real eBay sold comps, EN and JP) and TCGplayer market
    # (recent-sale based, EN only) are both sold-based; when both exist they
    # often differ by 10-20%, so average them rather than letting either one
    # silently win. Cardmarket EUR is listing-based, not sold — treated like
    # Browse below.
    tcg_price = (catalog_price if catalog_price is not None
                 and language.lower() == "english" else None)
    if pc_price is not None and tcg_price is not None:
        sold_base = (pc_price + tcg_price) / 2
        sold_label = "PriceCharting + TCGplayer avg"
    elif pc_price is not None:
        sold_base, sold_label = pc_price, "PriceCharting Ungraded"
    elif tcg_price is not None:
        sold_base, sold_label = tcg_price, catalog_label
    else:
        sold_base, sold_label = None, None

    # --- Sold anchor + trend blend -------------------------------------------
    if sold_base is not None and browse is not None:
        return _apply_trend(sold_base, sold_label, browse)

    if sold_base is not None:
        return RawPriceResult(nm_price=sold_base, baseline_label=sold_label, extra_note=None)

    if browse is not None:
        # No sold data — apply haircut to active-listing median to approximate clearing price
        market = browse["price"] * (1 - BROWSE_HAIRCUT)
        return RawPriceResult(
            nm_price=round(market, 2),
            baseline_label=f"eBay Browse (−{BROWSE_HAIRCUT:.0%} haircut, no sold data)",
            extra_note=(
                f"No sold-comp data found. Active listings ({browse['label']}): "
                f"${browse['price']:.2f}. Applied {BROWSE_HAIRCUT:.0%} haircut to "
                f"estimate clearing price: ${market:.2f}."
            ),
        )

    # Cardmarket EUR (JP listing-based) — apply haircut, better than nothing
    if catalog_price is not None:
        market = catalog_price * (1 - BROWSE_HAIRCUT)
        return RawPriceResult(
            nm_price=round(market, 2),
            baseline_label=f"{catalog_label} (−{BROWSE_HAIRCUT:.0%} haircut)",
            extra_note=None,
        )

    # Last resort: eBay sold HTML (almost always 403, but preserve as backstop)
    if language.lower() == "japanese":
        try:
            ebay = await ebay_lookup.lookup_raw_price(
                name, set_name, card_number, language="japanese", condition="NM",
            )
        except Exception as e:
            log.warning("eBay lookup fallback failed: %s", e)
            ebay = None
        if ebay and ebay.median_usd:
            return RawPriceResult(
                nm_price=float(ebay.median_usd),
                baseline_label="eBay sold (JP-keyword, last resort)",
                extra_note=(
                    f"n={ebay.sample_size}/{ebay.raw_sample_size}, "
                    f"{ebay.period_days}d window"
                ),
            )

    return RawPriceResult(nm_price=None, baseline_label="no data", extra_note=None)
