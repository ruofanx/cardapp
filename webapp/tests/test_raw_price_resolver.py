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
    """PC $100 + Browse $115 → +15% trend (capped) → market $115."""
    monkeypatch.setattr(rpr.pricecharting_lookup, "lookup_raw_price", _pc(100.0))
    monkeypatch.setattr(rpr.card_lookup, "lookup_card", _catalog(90.0))
    monkeypatch.setattr(rpr.ebay_browse_api, "median_relevant_price", _browse(115.0))

    result = asyncio.run(rpr.resolve_raw_price("Charizard", "Base Set", "4/102"))

    assert result.nm_price == 115.0
    assert "PriceCharting Ungraded" in result.baseline_label
    assert "rising" in result.baseline_label
    assert "rising" in result.extra_note


def test_pc_anchor_softening_trend(monkeypatch):
    """PC $100 + Browse $80 → -15% trend (capped) → market $85."""
    monkeypatch.setattr(rpr.pricecharting_lookup, "lookup_raw_price", _pc(100.0))
    monkeypatch.setattr(rpr.card_lookup, "lookup_card", _catalog(90.0))
    monkeypatch.setattr(rpr.ebay_browse_api, "median_relevant_price", _browse(80.0))

    result = asyncio.run(rpr.resolve_raw_price("Charizard", "Base Set", "4/102"))

    assert result.nm_price == 85.0
    assert "softening" in result.baseline_label


def test_pc_anchor_stable_market(monkeypatch):
    """PC $100 + Browse $103 → +3% trend → market $103."""
    monkeypatch.setattr(rpr.pricecharting_lookup, "lookup_raw_price", _pc(100.0))
    monkeypatch.setattr(rpr.card_lookup, "lookup_card", _catalog(90.0))
    monkeypatch.setattr(rpr.ebay_browse_api, "median_relevant_price", _browse(103.0))

    result = asyncio.run(rpr.resolve_raw_price("Charizard", "Base Set", "4/102"))

    assert result.nm_price == 103.0
    assert "stable" in result.baseline_label


def test_trend_cap_limits_upside(monkeypatch):
    """PC $100 + Browse $200 → capped at +15% → market $115, not $200."""
    monkeypatch.setattr(rpr.pricecharting_lookup, "lookup_raw_price", _pc(100.0))
    monkeypatch.setattr(rpr.card_lookup, "lookup_card", _no_catalog())
    monkeypatch.setattr(rpr.ebay_browse_api, "median_relevant_price", _browse(200.0))

    result = asyncio.run(rpr.resolve_raw_price("Pikachu", "Base Set", "58/102"))

    assert result.nm_price == 115.0


def test_trend_cap_limits_downside(monkeypatch):
    """PC $100 + Browse $20 → capped at -15% → market $85, not $20."""
    monkeypatch.setattr(rpr.pricecharting_lookup, "lookup_raw_price", _pc(100.0))
    monkeypatch.setattr(rpr.card_lookup, "lookup_card", _no_catalog())
    monkeypatch.setattr(rpr.ebay_browse_api, "median_relevant_price", _browse(20.0))

    result = asyncio.run(rpr.resolve_raw_price("Pikachu", "Base Set", "58/102"))

    assert result.nm_price == 85.0


# ---------------------------------------------------------------------------
# EN fallback: TCGplayer as sold base
# ---------------------------------------------------------------------------

def test_tcgplayer_as_sold_base_when_no_pc(monkeypatch):
    """No PC, TCGplayer $50, Browse $55 → +10% trend → market $55."""
    monkeypatch.setattr(rpr.pricecharting_lookup, "lookup_raw_price", _no_pc())
    monkeypatch.setattr(rpr.card_lookup, "lookup_card", _catalog(50.0))
    monkeypatch.setattr(rpr.ebay_browse_api, "median_relevant_price", _browse(55.0))

    result = asyncio.run(rpr.resolve_raw_price("Pikachu", "Base Set", "58/102"))

    assert result.nm_price == 55.0
    assert "TCGplayer" in result.baseline_label


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
