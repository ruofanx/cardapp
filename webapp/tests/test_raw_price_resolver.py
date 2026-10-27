"""Tests for raw_price_resolver — sold-anchor + listing-trend model."""
from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import raw_price_resolver as rpr


def _browse(median, n=8, low=None, high=None):
    low = low or median * 0.85
    high = high or median * 1.15
    async def fake(*args, **kwargs):
        return {"median_usd": median, "sample_size": n, "raw_sample_size": n + 2,
                "low_usd": low, "high_usd": high, "query": "test query"}
    return fake


def _no_browse():
    async def fake(*args, **kwargs):
        return None
    return fake


def _pc(price):
    async def fake(*args, **kwargs):
        return SimpleNamespace(price_usd=price)
    return fake


def _no_pc():
    async def fake(*args, **kwargs):
        return None
    return fake


def _catalog(price, source="tcgplayer"):
    async def fake(*args, **kwargs):
        return SimpleNamespace(market_price=price, source=source)
    return fake


def _no_catalog():
    async def fake(*args, **kwargs):
        return None
    return fake


# ---------------------------------------------------------------------------
# Sold anchor + trend blend
# ---------------------------------------------------------------------------

def test_pc_anchor_rising_trend(monkeypatch):
    """PC $100 + Browse $140 → asks ≈$123 after discount → capped, damped +7.5%."""
    monkeypatch.setattr(rpr.pricecharting_lookup, "lookup_raw_price", _pc(100.0))
    monkeypatch.setattr(rpr.card_lookup, "lookup_card", _no_catalog())
    monkeypatch.setattr(rpr.ebay_browse_api, "median_relevant_price", _browse(140.0))

    result = asyncio.run(rpr.resolve_raw_price("Charizard", "Base Set", "4/102"))

    assert result.nm_price == 107.5
    assert "PriceCharting Ungraded" in result.baseline_label
    assert "rising" in result.baseline_label
    assert "rising" in result.extra_note


def test_pc_anchor_softening_trend(monkeypatch):
    """PC $100 + Browse $70 → asks ≈$62 after discount → capped, damped -7.5%."""
    monkeypatch.setattr(rpr.pricecharting_lookup, "lookup_raw_price", _pc(100.0))
    monkeypatch.setattr(rpr.card_lookup, "lookup_card", _no_catalog())
    monkeypatch.setattr(rpr.ebay_browse_api, "median_relevant_price", _browse(70.0))

    result = asyncio.run(rpr.resolve_raw_price("Charizard", "Base Set", "4/102"))

    assert result.nm_price == 92.5
    assert "softening" in result.baseline_label


def test_pc_anchor_stable_market(monkeypatch):
    """Asks that clear at the sold price leave the anchor untouched."""
    monkeypatch.setattr(rpr.pricecharting_lookup, "lookup_raw_price", _pc(100.0))
    monkeypatch.setattr(rpr.card_lookup, "lookup_card", _no_catalog())
    monkeypatch.setattr(rpr.ebay_browse_api, "median_relevant_price", _browse(113.64))

    result = asyncio.run(rpr.resolve_raw_price("Charizard", "Base Set", "4/102"))

    assert result.nm_price == 100.0
    assert "stable" in result.baseline_label


def test_asks_slightly_above_sold_do_not_inflate_price(monkeypatch):
    """Regression: PC == TCGplayer == $92, asks $100 must not push FMV above sold."""
    monkeypatch.setattr(rpr.pricecharting_lookup, "lookup_raw_price", _pc(92.0))
    monkeypatch.setattr(rpr.card_lookup, "lookup_card", _catalog(92.0))
    monkeypatch.setattr(rpr.ebay_browse_api, "median_relevant_price", _browse(100.0))

    result = asyncio.run(rpr.resolve_raw_price("Venusaur ex", "151", "198"))

    assert 89.0 <= result.nm_price <= 92.0


def test_trend_cap_limits_upside(monkeypatch):
    """PC $100 + Browse $200 → capped, damped at +7.5% → $107.50, not $200."""
    monkeypatch.setattr(rpr.pricecharting_lookup, "lookup_raw_price", _pc(100.0))
    monkeypatch.setattr(rpr.card_lookup, "lookup_card", _no_catalog())
    monkeypatch.setattr(rpr.ebay_browse_api, "median_relevant_price", _browse(200.0))

    result = asyncio.run(rpr.resolve_raw_price("Pikachu", "Base Set", "58/102"))

    assert result.nm_price == 107.5


def test_trend_cap_limits_downside(monkeypatch):
    """PC $100 + Browse $20 → capped, damped at -7.5% → $92.50, not $20."""
    monkeypatch.setattr(rpr.pricecharting_lookup, "lookup_raw_price", _pc(100.0))
    monkeypatch.setattr(rpr.card_lookup, "lookup_card", _no_catalog())
    monkeypatch.setattr(rpr.ebay_browse_api, "median_relevant_price", _browse(20.0))

    result = asyncio.run(rpr.resolve_raw_price("Pikachu", "Base Set", "58/102"))

    assert result.nm_price == 92.5


def test_browse_signal_is_nm_only(monkeypatch):
    """Raw pricing must ask Browse for NM-condition listings only."""
    seen = {}

    async def fake(*args, **kwargs):
        seen.update(kwargs)
        return None

    monkeypatch.setattr(rpr.pricecharting_lookup, "lookup_raw_price", _pc(10.0))
    monkeypatch.setattr(rpr.card_lookup, "lookup_card", _no_catalog())
    monkeypatch.setattr(rpr.ebay_browse_api, "median_relevant_price", fake)

    asyncio.run(rpr.resolve_raw_price("Pikachu", "Base Set", "58/102"))

    assert seen.get("nm_only") is True


# ---------------------------------------------------------------------------
# EN fallback: TCGplayer as sold base
# ---------------------------------------------------------------------------

def test_tcgplayer_as_sold_base_when_no_pc(monkeypatch):
    """No PC, TCGplayer $50, Browse $55 → asks ≈$48.40 → -1.6% → $49.20."""
    monkeypatch.setattr(rpr.pricecharting_lookup, "lookup_raw_price", _no_pc())
    monkeypatch.setattr(rpr.card_lookup, "lookup_card", _catalog(50.0))
    monkeypatch.setattr(rpr.ebay_browse_api, "median_relevant_price", _browse(55.0))

    result = asyncio.run(rpr.resolve_raw_price("Pikachu", "Base Set", "58/102"))

    assert result.nm_price == 49.2
    assert "TCGplayer" in result.baseline_label


def test_pc_and_tcgplayer_are_averaged(monkeypatch):
    """Both sold signals present → anchor is their mean, not PC alone."""
    monkeypatch.setattr(rpr.pricecharting_lookup, "lookup_raw_price", _pc(100.0))
    monkeypatch.setattr(rpr.card_lookup, "lookup_card", _catalog(120.0))
    monkeypatch.setattr(rpr.ebay_browse_api, "median_relevant_price", _no_browse())

    result = asyncio.run(rpr.resolve_raw_price("Vaporeon ex", "Prismatic Evolutions", "149"))

    assert result.nm_price == 110.0
    assert result.baseline_label == "PriceCharting + TCGplayer avg"


def test_tcgplayer_alone_no_browse(monkeypatch):
    """No PC, TCGplayer $50, no Browse → $50."""
    monkeypatch.setattr(rpr.pricecharting_lookup, "lookup_raw_price", _no_pc())
    monkeypatch.setattr(rpr.card_lookup, "lookup_card", _catalog(50.0))
    monkeypatch.setattr(rpr.ebay_browse_api, "median_relevant_price", _no_browse())

    result = asyncio.run(rpr.resolve_raw_price("Pikachu", "Base Set", "58/102"))

    assert result.nm_price == 50.0
    assert result.extra_note is None


# ---------------------------------------------------------------------------
# PC-only (no Browse)
# ---------------------------------------------------------------------------

def test_pc_only_no_browse(monkeypatch):
    """PC $30, no Browse → $30 (no trend adjustment)."""
    monkeypatch.setattr(rpr.pricecharting_lookup, "lookup_raw_price", _pc(30.0))
    monkeypatch.setattr(rpr.card_lookup, "lookup_card", _no_catalog())
    monkeypatch.setattr(rpr.ebay_browse_api, "median_relevant_price", _no_browse())

    result = asyncio.run(rpr.resolve_raw_price(
        "Lugia", "Neo Genesis", "9/111", language="japanese",
    ))

    assert result.nm_price == 30.0
    assert result.baseline_label == "PriceCharting Ungraded"
    assert result.extra_note is None


# ---------------------------------------------------------------------------
# Browse-only fallback (apply haircut)
# ---------------------------------------------------------------------------

def test_browse_only_applies_haircut(monkeypatch):
    """No PC, no catalog, Browse $100 → $88 (12% haircut)."""
    monkeypatch.setattr(rpr.pricecharting_lookup, "lookup_raw_price", _no_pc())
    monkeypatch.setattr(rpr.card_lookup, "lookup_card", _no_catalog())
    monkeypatch.setattr(rpr.ebay_browse_api, "median_relevant_price", _browse(100.0))
    monkeypatch.setattr(rpr.ebay_lookup, "lookup_raw_price", _no_pc())

    result = asyncio.run(rpr.resolve_raw_price(
        "Umbreon", "Neo Discovery", "13/75", language="japanese",
    ))

    assert result.nm_price == 88.0
    assert "haircut" in result.baseline_label
    assert "haircut" in result.extra_note


# ---------------------------------------------------------------------------
# JP: Cardmarket EUR as last catalog fallback (with haircut)
# ---------------------------------------------------------------------------

def test_cardmarket_jp_fallback_with_haircut(monkeypatch):
    """No PC, no Browse, Cardmarket EUR $100 (JP listing-based) → $88 haircut."""
    monkeypatch.setattr(rpr.pricecharting_lookup, "lookup_raw_price", _no_pc())
    monkeypatch.setattr(rpr.card_lookup, "lookup_card", _catalog(100.0, source="cardmarket-jp"))
    monkeypatch.setattr(rpr.ebay_browse_api, "median_relevant_price", _no_browse())
    monkeypatch.setattr(rpr.ebay_lookup, "lookup_raw_price", _no_pc())

    result = asyncio.run(rpr.resolve_raw_price(
        "Pikachu", "Base Set", "58/102", language="japanese",
    ))

    assert result.nm_price == 88.0
    assert "haircut" in result.baseline_label


# ---------------------------------------------------------------------------
# No data at all
# ---------------------------------------------------------------------------

def test_no_data_returns_none(monkeypatch):
    """All sources return nothing → nm_price is None."""
    monkeypatch.setattr(rpr.pricecharting_lookup, "lookup_raw_price", _no_pc())
    monkeypatch.setattr(rpr.card_lookup, "lookup_card", _no_catalog())
    monkeypatch.setattr(rpr.ebay_browse_api, "median_relevant_price", _no_browse())
    monkeypatch.setattr(rpr.ebay_lookup, "lookup_raw_price", _no_pc())

    result = asyncio.run(rpr.resolve_raw_price("Unknown", "Unknown", "1/1"))

    assert result.nm_price is None
    assert result.baseline_label == "no data"


# ---------------------------------------------------------------------------
# JP: TCGplayer EN prices are skipped (not JP market)
# ---------------------------------------------------------------------------

def test_tcgplayer_skipped_for_jp_cards(monkeypatch):
    """TCGplayer prices are EN-market only — should not anchor JP cards."""
    monkeypatch.setattr(rpr.pricecharting_lookup, "lookup_raw_price", _no_pc())
    # catalog returns TCGplayer price for a JP card — should be ignored
    monkeypatch.setattr(rpr.card_lookup, "lookup_card", _catalog(50.0, source="tcgplayer"))
    monkeypatch.setattr(rpr.ebay_browse_api, "median_relevant_price", _browse(80.0))
    monkeypatch.setattr(rpr.ebay_lookup, "lookup_raw_price", _no_pc())

    result = asyncio.run(rpr.resolve_raw_price(
        "Pikachu", "Base Set", "58/102", language="japanese",
    ))

    # Should use Browse with haircut, not TCGplayer as anchor
    assert result.nm_price == round(80.0 * (1 - rpr.BROWSE_HAIRCUT), 2)
    assert "haircut" in result.baseline_label
